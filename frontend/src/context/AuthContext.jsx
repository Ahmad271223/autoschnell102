import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { aboNeuLadenAnmelden, api } from "@/lib/api";
import { lesen, lokalerSpeicher, schreiben, sitzungsSpeicher } from "@/lib/speicher";
import { vergleichLeeren } from "@/lib/vergleichSpeicher";
import { TOKEN_APP, TOKEN_KAEUFER, tokenLesen, tokenLoeschen, tokenSetzen } from "@/lib/sitzung";
import { verbindungsGrund } from "@/components/VerbindungsFehler";

const AuthCtx = createContext(null);

/*
 * Rollenpruefung 22.09.2026 (RP-557): "bekanntes Geraet". Nach der ersten
 * erfolgreichen Anmeldung schickt der Server einen zufaelligen Geraete-
 * Schluessel (geraet_id); das Geraet legt ihn ab und schickt ihn bei jeder
 * Anmeldung mit. Ein bekanntes Geraet ist — wie eine bekannte IP — von der
 * Konto-Sperre nach vielen Fehlversuchen entlastet: ein Angreifer kann so ein
 * Konto (auch das des Betreibers) nicht mehr fuer ALLE neuen IPs aussperren,
 * etwa fuer Sucher im Mobilnetz. Der Schluessel gilt fuer alle Konten dieses
 * Browsers; am Konto liegt nur sein HMAC. Kein Speicher (Privatmodus) = wie
 * bisher, nur ohne Entlastung.
 */
export const GERAET_SCHLUESSEL = "as_geraet_id";
const GERAET_MUSTER = /^[A-Za-z0-9_-]{16,64}$/;

export function geraetIdLesen() {
  const wert = lesen(lokalerSpeicher(), GERAET_SCHLUESSEL, "");
  return GERAET_MUSTER.test(wert || "") ? wert : undefined;
}

export function geraetIdMerken(antwort) {
  const wert = antwort?.geraet_id;
  if (typeof wert === "string" && GERAET_MUSTER.test(wert)) {
    schreiben(lokalerSpeicher(), GERAET_SCHLUESSEL, wert);
  }
}

/*
 * Rollenprüfung 22.09.2026 (Review): Zu welcher SITZUNG gehoert ein Token?
 * Seit RP-546 ersetzt jede Antwort mit X-Neues-Token (lib/api ->
 * sitzung.tokenErneuern) das Token dieses Tabs durch ein frisches Token
 * DERSELBEN Sitzung — gleiches Konto (sub), gleiche Sitzungs-ID (sid). Die
 * Regel "anderes Token = andere Anmeldung" warf danach beim naechsten
 * Netzaussetzer die laufende Seite weg ("Keine Verbindung" im Vollbild).
 * Verglichen wird deshalb die Sitzung aus dem Token-Inhalt. Der Inhalt wird
 * NICHT geprueft — er dient nur dem Wiedererkennen der eigenen Sitzung, nie
 * einer Berechtigung (die prueft allein der Server). Ist das Token nicht
 * lesbar, zaehlt wie bisher das Token selbst.
 */
export function sitzungVonToken(token) {
  if (!token) return null;
  try {
    const teil = String(token).split(".")[1] || "";
    const b64 = teil.replace(/-/g, "+").replace(/_/g, "/");
    const inhalt = JSON.parse(atob(b64 + "===".slice((b64.length + 3) % 4)));
    const { sub, sid } = inhalt || {};
    if (typeof sub === "string" && sub && typeof sid === "string" && sid) {
      return `s:${sub}|${sid}`;
    }
  } catch {
    /* nicht lesbar: das Token selbst vergleichen */
  }
  return `t:${token}`;
}

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
  // Rollenprüfung 22.09.2026 (Review): gemerkt wird die SITZUNG des Tokens
  // (sitzungVonToken) — ein verlaengertes Token derselben Sitzung ist keine
  // andere Anmeldung.
  const geladenFuerSitzung = useRef(null);
  // Pruefbericht 20.09.2026 (B2): Warum hat der letzte refresh() keinen
  // Nutzer geliefert? login() braucht das — vorher meldete die Anmeldung
  // "Willkommen zurueck", obwohl /auth/me die Sitzung gerade abgelehnt
  // hatte (gesperrte Firma), und der Nutzer stand wortlos wieder auf der
  // leeren Anmeldemaske.
  const letzteAblehnung = useRef("");

  const refresh = useCallback(async () => {
    const token = tokenLesen(TOKEN_APP);
    if (!token) {
      geladenFuerSitzung.current = null;
      setUser(null);
      setDealer(null);
      setSubscription(null);
      setVerbindungsfehler(null);
      setLoading(false);
      return null;
    }
    try {
      const { data } = await api.get("/auth/me");
      geladenFuerSitzung.current = sitzungVonToken(token);
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
        // Schon geladen und dieselbe Sitzung (auch mit verlaengertem Token)
        // -> Seite laeuft weiter (siehe geladenFuerSitzung). Nur beim ersten
        // Laden bleibt user null und ProtectedRoute zeigt die Meldung.
        if (geladenFuerSitzung.current
            && geladenFuerSitzung.current === sitzungVonToken(tokenLesen(TOKEN_APP))) {
          setLoading(false);
          return null;
        }
      }
      geladenFuerSitzung.current = null;
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
  // U-143: bei einer 402 irgendwo in der App den Abo-Stand neu laden.
  useEffect(() => {
    aboNeuLadenAnmelden(() => { refresh(); });
    return () => aboNeuLadenAnmelden(null);
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
    const { data } = await api.post("/auth/login",
      { kontonummer: kennung, password, geraet_id: geraetIdLesen() });
    geraetIdMerken(data);
    if (data?.mfa_erforderlich) {
      // Zwei-Faktor (Admin/Super-Admin): noch kein Sitzungs-Token — die
      // Login-Seite fragt jetzt den Code aus der Authenticator-App ab.
      return { mfa_erforderlich: true, mfa_token: data.mfa_token };
    }
    if (data?.user?.role === "b2b_buyer") {
      // Pruefbericht 20.09.2026 (U-145): Zwischenhaendler gehoeren in den
      // Marktplatz, dessen Anmeldung TOKEN_KAEUFER liest. Vorher landete das
      // Token unter TOKEN_APP — /markt kannte niemanden, und es hiess erneut
      // anmelden. Login.jsx laedt danach den Marktplatz-Stand nach.
      tokenSetzen(TOKEN_KAEUFER, data.token);
      return data.user;
    }
    tokenSetzen(TOKEN_APP, data.token, { nurSitzung: !!data.user?.is_super_admin });
    await sitzungPruefen();
    return data.user;
  };
  const loginMfa = async (mfaToken, code) => {
    const { data } = await api.post("/auth/login/mfa",
      { mfa_token: mfaToken, code, geraet_id: geraetIdLesen() });
    geraetIdMerken(data);
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
    geladenFuerSitzung.current = null;
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
