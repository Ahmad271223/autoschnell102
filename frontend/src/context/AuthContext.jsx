import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { sitzungsSpeicher } from "@/lib/speicher";
import { vergleichLeeren } from "@/lib/vergleichSpeicher";
import { TOKEN_APP, tokenLesen, tokenLoeschen, tokenSetzen } from "@/lib/sitzung";
import { verbindungsGrund } from "@/components/VerbindungsFehler";

const AuthCtx = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [dealer, setDealer] = useState(null);
  const [subscription, setSubscription] = useState(null);
  const [loading, setLoading] = useState(true);
  // Runde 22 (11.09.2026): /auth/me hat nicht geantwortet (Funkloch,
  // Timeout, 502/520 im Rollout) oder mit 5xx/429 — die Anmeldung ist
  // NICHT beendet, der Token bleibt. null = alles gut, sonst
  // { status, detail } (status null = gar keine Antwort). ProtectedRoute
  // zeigt dann "Keine Verbindung" statt Login.
  const [verbindungsfehler, setVerbindungsfehler] = useState(null);
  // Runde 22 (11.09.2026, Gegenpruefung): Fuer welchen Token sind user/
  // dealer/subscription geladen? Scheitert ein SPAETERES refresh() am Netz
  // (nach dem Speichern der Einstellungen, beim Abfragen der Zahlung),
  // bleiben die Daten stehen — sonst ersetzte ein einziger Aussetzer die
  // laufende Seite durch die Vollbild-Meldung. Hat sich der Token
  // inzwischen geaendert (login() mit anderem Konto im selben Tab), werden
  // die alten Daten verworfen, damit nicht Konto A mit dem Token von B
  // stehen bleibt.
  const geladenFuerToken = useRef(null);
  // Pruefbericht 20.09.2026 (B2): Warum hat der letzte refresh() keinen
  // Nutzer geliefert? login() braucht das — vorher meldete die Anmeldung
  // "Willkommen zurueck", obwohl /auth/me die Sitzung gerade abgelehnt
  // hatte (gesperrte Firma), und der Nutzer stand wortlos wieder auf der
  // leeren Anmeldemaske.
  const letzteAblehnung = useRef("");

  const refresh = useCallback(async () => {
    const token = tokenLesen(TOKEN_APP);
    if (!token) {
      geladenFuerToken.current = null;
      setUser(null);
      setDealer(null);
      setSubscription(null);
      setVerbindungsfehler(null);
      setLoading(false);
      return null;
    }
    try {
      const { data } = await api.get("/auth/me");
      geladenFuerToken.current = token;
      letzteAblehnung.current = "";
      setUser(data.user);
      setDealer(data.dealer);
      setSubscription(data.subscription);
      setVerbindungsfehler(null);
      setLoading(false);
      return data;
    } catch (e) {
      // Runde 22 (11.09.2026): Vorher wurde bei JEDEM Fehler abgemeldet —
      // auch bei Funkloch oder Server-Neustart. Sucher mussten sich neu
      // anmelden, und wegen Single-Session flog ihre andere Sitzung raus.
      // Jetzt nur noch, wenn der Server die Sitzung wirklich ablehnt:
      // 401 (beendet/abgelaufen) oder 403 (Firma gesperrt).
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail;
      letzteAblehnung.current = typeof detail === "string" && detail ? detail : "";
      if (status === 401 || status === 403) {
        // Bei 401 hat der Interceptor in api.js den Token schon geloescht
        // (samt Abmeldegrund und Umleitung). Nur loeschen, solange dieser
        // Tab noch einen hat: ein zweites tokenLoeschen ohne eigenen Token
        // wuerde die "letzte Anmeldung" eines ANDEREN Tabs aus localStorage
        // entfernen (siehe sitzung.js).
        if (tokenLesen(TOKEN_APP)) tokenLoeschen(TOKEN_APP);
        setVerbindungsfehler(null);
      } else {
        setVerbindungsfehler(verbindungsGrund(e));
        // Schon geladen und derselbe Token -> Seite laeuft weiter (siehe
        // geladenFuerToken). Nur beim ersten Laden bleibt user null und
        // ProtectedRoute zeigt die Meldung.
        if (geladenFuerToken.current && geladenFuerToken.current === tokenLesen(TOKEN_APP)) {
          setLoading(false);
          return null;
        }
      }
      geladenFuerToken.current = null;
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

  // Nach dem Token noch /auth/me: erst wenn das klappt, ist die Anmeldung
  // wirklich durch. Sonst einen Fehler MIT dem Text des Servers werfen —
  // die Anmeldeseite zeigt ihn statt "Willkommen zurueck" (B2).
  const sitzungPruefen = async () => {
    const me = await refresh();
    if (me) return me;
    const fehler = new Error(letzteAblehnung.current
      || "Die Anmeldung konnte nicht abgeschlossen werden – bitte erneut versuchen.");
    throw fehler;
  };

  // Kontonummer (13.09.2026): Anmeldekennung ist die Kontonummer (bzw. der
  // Benutzername des Betreibers). Konten legt nur der Betreiber an — die
  // fruehere register()-Funktion gibt es nicht mehr.
  const login = async (kennung, password) => {
    const { data } = await api.post("/auth/login", { kontonummer: kennung, password });
    if (data?.mfa_erforderlich) {
      // Zwei-Faktor (Admin/Super-Admin): noch kein Sitzungs-Token — die
      // Login-Seite fragt jetzt den Code aus der Authenticator-App ab.
      return { mfa_erforderlich: true, mfa_token: data.mfa_token };
    }
    tokenSetzen(TOKEN_APP, data.token, { nurSitzung: !!data.user?.is_super_admin });
    await sitzungPruefen();
    return data.user;
  };
  const loginMfa = async (mfaToken, code) => {
    const { data } = await api.post("/auth/login/mfa", { mfa_token: mfaToken, code });
    tokenSetzen(TOKEN_APP, data.token, { nurSitzung: !!data.user?.is_super_admin });
    await sitzungPruefen();
    return data.user;
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch (_) {}
    tokenLoeschen(TOKEN_APP);
    // Runde 27: auch fremde Staende. 20.09.2026 (B1/B4): ueber den sicheren
    // Zugriff — der blanke window.sessionStorage wirft bei gesperrtem
    // Speicher, und dann haette das Abmelden selbst abgebrochen.
    vergleichLeeren(sitzungsSpeicher());
    geladenFuerToken.current = null;
    setUser(null);
    setDealer(null);
    setSubscription(null);
    setVerbindungsfehler(null);
  };

  return (
    <AuthCtx.Provider value={{ user, dealer, subscription, loading, verbindungsfehler, login, loginMfa, logout, refresh, setDealer }}>
      {children}
    </AuthCtx.Provider>
  );
};

export const useAuth = () => useContext(AuthCtx);
