import { fassungMithoeren } from "@/lib/fassung";
import { createContext, useContext, useEffect, useState } from "react";
import { TOKEN_FAHRER, tokenLesen, tokenLoeschen, tokenSetzen } from "@/lib/sitzung";
import axios from "axios";
import {
  API_BASE, blobFehlerLesbar, gehoertZumAktuellenToken, istLangeAktion, LANGE_AKTION_MS,
  neuesTokenUebernehmen, openAuthedFile,
} from "@/lib/api";
import { schreiben, sitzungsSpeicher } from "@/lib/speicher";
import { verbindungsGrund } from "@/components/VerbindungsFehler";
import { geraetIdLesen, geraetIdMerken } from "@/context/AuthContext";

/**
 * Rollenprüfung 22.09.2026 (RP-546): gleitende Sitzung. Läuft das Fahrer-Token
 * bald ab, schickt der Server ein frisches derselben Sitzung in der Kopfzeile
 * X-Neues-Token (routes/drivers.current_driver). Vorher endete jede Sitzung
 * hart nach 7 Tagen — auch mitten beim Unterschreiben vor Ort.
 * Abgelegt wird es nur, wenn die Anfrage mit dem Token lief, das JETZT gilt:
 * eine späte Antwort nach dem Abmelden oder nach einer Anmeldung mit einem
 * anderen Konto darf kein Token zurückbringen.
 * Rollenprüfung 22.09.2026 (RP-546, Nachtrag): Vorher legte tokenSetzen das
 * Token ab — das ist die Funktion für eine NEUE Anmeldung: sie überschrieb
 * LETZTE_ANMELDUNG (der App-Start öffnete danach die Fahrer-Anmeldung, auch
 * wenn zuletzt z. B. der Händler angemeldet war) und ersetzte in
 * localStorage eine neuere Anmeldung aus einem anderen Tab. Jetzt dieselbe
 * Regel wie Händler- und Käufer-App (lib/api.neuesTokenUebernehmen ->
 * sitzung.tokenErneuern): nur dort schreiben, wo noch das alte Token liegt,
 * LETZTE_ANMELDUNG nie anfassen, nur JWT-förmige Werte übernehmen.
 */
export function neuesFahrerTokenAblegen(antwort) {
  return neuesTokenUebernehmen(antwort, TOKEN_FAHRER);
}

/**
 * Fahrer-Auth (eigenständige Accounts, separat vom Händler-Auth).
 * LocalStorage-Key: ah_driver_token.
 */
const DriverCtx = createContext(null);

// RP-546: Ziel nach der Neuanmeldung (nur Protokollseiten, nur dieser Tab).
export const FAHRER_RUECKSPRUNG = "ah_fahrer_ruecksprung";

/** Gemerktes Rücksprungziel holen (und vergessen) — nur /fahrer/protokoll/<id>. */
export function fahrerRuecksprungHolen() {
  const speicher = sitzungsSpeicher();
  let ziel = "";
  try {
    ziel = speicher?.getItem(FAHRER_RUECKSPRUNG) || "";
    speicher?.removeItem(FAHRER_RUECKSPRUNG);
  } catch { /* egal */ }
  return /^\/fahrer\/protokoll\/[^/]+$/.test(ziel) ? ziel : "";
}

export const driverApi = axios.create({ baseURL: API_BASE, timeout: 60000 });
// Runde 31: eigene Verbindung — sonst bliebe dieser Bereich blind fuer neue Fassungen.
fassungMithoeren(driverApi);
driverApi.interceptors.request.use((c) => {
  const t = tokenLesen(TOKEN_FAHRER);
  if (t) c.headers.Authorization = `Bearer ${t}`;
  // Gegenpruefung 12.09.2026: Die Fahrer-App hat eine EIGENE Verbindung und
  // bekam das laengere Zeitlimit nicht — Abholprotokoll und Kaufvertrag sind
  // aber genau die PDFs, die am laengsten brauchen.
  if (istLangeAktion(c)) c.timeout = LANGE_AKTION_MS;
  return c;
});

// Pruefbericht 20.09.2026 (R1-38/R1-27): Die Fahrer-App hatte keinen
// 401-Abfaenger. Endete die Sitzung (neue Anmeldung auf einem anderen Handy,
// Sperre, Passwort neu gesetzt), blieb der Fahrer auf der Seite, jede Aktion
// scheiterte still, und er erfuhr nie, warum. Jetzt: Token weg, Grund
// merken, zur Fahrer-Anmeldung — wie in der Haendler- und Kaeufer-App.
driverApi.interceptors.response.use(
  (r) => {
    // RP-546: verlängertes Token ablegen (siehe neuesFahrerTokenAblegen).
    try { neuesFahrerTokenAblegen(r); } catch { /* Kür, nie ein Fehler */ }
    return r;
  },
  async (err) => {
    await blobFehlerLesbar(err);             // M34: Klartext auch bei PDF-Abrufen
    const url = String(err?.config?.url || "");
    if (err?.response?.status === 401
        && tokenLesen(TOKEN_FAHRER)
        && gehoertZumAktuellenToken(err?.config, tokenLesen(TOKEN_FAHRER))
        && !url.includes("/driver/login")) {
      tokenLoeschen(TOKEN_FAHRER);
      const detail = err?.response?.data?.detail;
      schreiben(sitzungsSpeicher(), "ah_fahrer_abmeldegrund",
                typeof detail === "string" && detail ? detail : "");
      const pfad = window.location.pathname;
      // Rollenprüfung 22.09.2026 (RP-546): aus dem Abholprotokoll heraus nach
      // der Neuanmeldung genau dorthin zurück — die Seite stellt Eingaben und
      // Unterschriften aus der Sicherung im Tab wieder her.
      if (/^\/fahrer\/protokoll\/[^/]+$/.test(pfad)) {
        schreiben(sitzungsSpeicher(), FAHRER_RUECKSPRUNG, pfad);
      }
      if (pfad.startsWith("/fahrer") && !pfad.startsWith("/fahrer/login")) {
        window.location.href = "/fahrer/login?reason=session";
      }
    }
    return Promise.reject(err);
  },
);

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

  // Kontonummer (13.09.2026): Anmeldung mit der Kontonummer. Fahrer-Konten
  // legt der Betreiber an — keine Selbstregistrierung mehr.
  const login = async (kennung, password) => {
    // Rollenprüfung 22.09.2026 (RP-557): "bekanntes Gerät" wie in der
    // Händler-App — der Schlüssel entlastet dieses Handy von der Konto-Sperre
    // nach fremden Fehlversuchen (Fahrer im Mobilnetz wechseln ständig die IP).
    const { data } = await driverApi.post("/driver/login",
      { kontonummer: kennung, password, geraet_id: geraetIdLesen() });
    geraetIdMerken(data);
    tokenSetzen(TOKEN_FAHRER, data.token);
    // volle /me-Payload holen (inkl. dealers)
    // Pruefung 14.09.2026 (A5): Scheitert /me nach dem Speichern des Tokens,
    // blieb ein Token ohne Fahrerdaten zurueck (Seite halb angemeldet). Lehnt
    // der Server die Sitzung ab, Token weg; bei Netzfehlern bleibt er, die
    // Layout-Anzeige "Keine Verbindung" greift (wie beim Start).
    let me;
    try {
      me = await driverApi.get("/driver/me");
    } catch (e) {
      const status = e?.response?.status;
      if (status === 401 || status === 403) tokenLoeschen(TOKEN_FAHRER);
      else setFehler(verbindungsGrund(e));
      throw e;
    }
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
    // Rollenprüfung 22.09.2026 (RP-500): Der Request-Interceptor läuft in
    // axios 1.x asynchron — er las den Token erst NACH tokenLoeschen, der
    // Logout ging ohne Authorization raus (401), und die Sitzung blieb auf
    // dem Server gültig. Jetzt den Token vorher lesen und selbst mitschicken.
    const t = tokenLesen(TOKEN_FAHRER);
    if (t) {
      driverApi.post("/driver/logout", null, { headers: { Authorization: `Bearer ${t}` } })
        .catch(() => {});
    }
    tokenLoeschen(TOKEN_FAHRER);
    setDriver(null);
    setFehler(null);
  };

  return (
    <DriverCtx.Provider value={{ driver, ready, fehler, login, logout, refresh }}>
      {children}
    </DriverCtx.Provider>
  );
}

export const useDriver = () => useContext(DriverCtx);
