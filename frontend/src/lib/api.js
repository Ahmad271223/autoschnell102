import { fassungMithoeren } from "@/lib/fassung";
import { vergleichLeeren } from "@/lib/vergleichSpeicher";
import axios from "axios";
import { TOKEN_APP, tokenErneuern, tokenLesen, tokenLoeschen } from "@/lib/sitzung";
import { schreiben, sitzungsSpeicher } from "@/lib/speicher";
import { blobOeffnen } from "@/lib/dateiOeffnen";
import { BEREICH_ADMIN, BEREICH_FIRMA, bereichVonPfad } from "@/lib/rollen";
import { ungespeichertVerwerfen } from "@/lib/ungespeichert";

const BACKEND = process.env.REACT_APP_BACKEND_URL;
export const API_BASE = `${BACKEND}/api`;

// 60 s Timeout: haengt der Server, bekommt der Nutzer eine Fehlermeldung
// statt eines endlosen Spinners (Vergleich + PDF sind die langsamsten Wege).
export const api = axios.create({ baseURL: API_BASE, timeout: 60000 });
// Runde 31: jede Antwort traegt X-AH-Fassung — so erfaehrt die Oberflaeche
// von einer neuen Fassung, bevor sie gegen eine fehlende Datei laeuft.
fassungMithoeren(api);

// Runde 29 (12.09.2026, Pruefbefund): 60 s passen fuer die schnellen Wege,
// sind aber zu knapp fuer die langsamen. nginx laesst Anfragen bis 300 s
// laufen — der Browser brach nach 60 s ab, der Nutzer sah einen Fehler,
// obwohl der Server weiterarbeitete (und der Vertrag am Ende doch entstand).
// Betroffen sind das Erzeugen des Kaufvertrags und alle Datei-Abrufe (PDF).
//
// Hinweis (Gegenpruefung): In Produktion liegt Cloudflare davor und kappt
// eine Anfrage nach rund 100 s mit HTTP 524. Dieses Limit wirkt also als
// Netz fuer den Browser, nicht als Verlaengerung darueber hinaus — es
// verhindert vor allem, dass der Browser VOR dem Server aufgibt.
//
// Pruefbericht 20.09.2026 (DP-04): 180 s lagen ueber dem Cloudflare-Abbruch —
// der Nutzer bekam bei langsamen Abrufen die rohe 524-Seite statt einer
// Meldung. Jetzt 95 s: der Browser gibt knapp VOR Cloudflare auf und zeigt
// die deutsche Meldung (errMsg); der Server arbeitet im Hintergrund weiter.
export const LANGE_AKTION_MS = 95000;

/** DP-04: Meldung, wenn ein Fahrzeug-Abruf ins Zeitlimit laeuft. */
export const ABRUF_ZEITUEBERSCHREITUNG =
  "Der Abruf dauert zu lange — bitte in einer Minute noch einmal versuchen; "
  + "der Link wird im Hintergrund weitergeladen.";

/** Ist das ein Fahrzeug-Abruf (der Server laedt den Link im Hintergrund weiter)? */
export function istAbruf(config = {}) {
  const pfad = String(config?.url || "").split("?")[0];
  return /^\/(mobile\/compare|listings\/(check|ingest|resolve))(\/|$)/.test(pfad);
}

/** Braucht diese Anfrage das lange Zeitlimit? (rein, damit testbar) */
export function istLangeAktion(config = {}) {
  if (config.responseType === "blob") return true;
  const pfad = String(config.url || "");
  const methode = String(config.method || "get").toLowerCase();
  if (methode !== "post") return false;
  // Lange Wege auf der Serverseite: Vertrag erzeugen (PDF + Fotos) und der
  // Protokoll-Abschluss (Abholprotokoll-PDF + zwei Unterschrift-Bilder).
  // Abnahme 12.09.2026: Der Abschluss fehlte hier — ausgerechnet der
  // langsamste Weg der Fahrer-App lief weiter ins 60-Sekunden-Limit.
  return /^\/contracts(\/|$|\?)/.test(pfad)
    || /\/protocol\/(finalize|submit|correction)(\?|$)/.test(pfad)
    || /\/report(\?|$)/.test(pfad);
}

api.interceptors.request.use((config) => {
  const token = tokenLesen(TOKEN_APP);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  if (istLangeAktion(config)) config.timeout = LANGE_AKTION_MS;
  return config;
});

/**
 * Gehoert diese 401 noch zum Token, der GERADE gespeichert ist?
 *
 * Nachpruefung 20.09.2026 (Nr. 39/40): Der Abfaenger loeschte bei jeder 401
 * einfach den aktuell gespeicherten Token — ohne zu pruefen, ob die
 * gescheiterte Anfrage ueberhaupt mit DIESEM Token losgeschickt wurde.
 * Der Ablauf, der dabei schiefging:
 *   1. Eine Anfrage mit Token A ist unterwegs.
 *   2. Im selben Tab meldet sich jemand als Konto B an -> Token B liegt jetzt.
 *   3. Die alte Anfrage antwortet verspaetet mit 401 (Token A ist ja weg).
 *   4. Der Abfaenger loeschte Token B und warf Konto B zurueck zum Login.
 * Jetzt zaehlt eine 401 nur noch fuer den Token, mit dem sie gesendet wurde.
 * (Rein exportiert, damit es sich pruefen laesst.)
 */
export function gehoertZumAktuellenToken(config, aktuell) {
  const gesendet = String(config?.headers?.Authorization || "");
  if (!gesendet) return true;      // ohne Token gesendet: nichts zu schuetzen
  return gesendet === `Bearer ${aktuell || ""}`;
}

/**
 * Loest eine 401 auf diesem Weg die Abmeldung samt Umleitung aus?
 *
 * Rollenprüfung 22.09.2026 (RP-012/RP-111/RP-262): Beim Abmelden mit einer
 * schon beendeten Sitzung antwortet POST /auth/logout mit 401. Der Abfaenger
 * merkte dann "Sitzung beendet" vor und leitete hart auf /login um, waehrend
 * logout() und der Aufrufer selbst weiterleiteten — doppelte Umleitung und
 * eine falsche Meldung, obwohl der Nutzer sich selbst abgemeldet hat. Anmelden
 * (/auth/login, /auth/login/mfa) und Abmelden erledigen ihren Rest selbst.
 */
export function loestAbmeldungAus(url) {
  const pfad = String(url || "").split("?")[0];
  return !/\/auth\/(login|logout)(\/|$)/.test(pfad);
}

/**
 * Ist das die 403 einer gesperrten Firma (Kopfzeile `X-Sperre: firma`)?
 * Nur diese eine 403 fuehrt zur Abmeldung — jede andere 403 ("nur der
 * Hauptaccount darf das") bleibt ein normaler Fehler der Seite.
 */
export function istFirmensperre(err) {
  const r = err?.response;
  if (!r || r.status !== 403) return false;
  const kopf = r.headers?.["x-sperre"] ?? r.headers?.["X-Sperre"];
  return String(kopf || "") === "firma";
}

/**
 * Liegt diese Adresse in einem Bereich, den nur ein angemeldetes Firmen-
 * oder Betreiber-Konto sieht? Dann fuehrt eine beendete Sitzung zur Anmeldung.
 *
 * Pruefbericht 20.09.2026 (U-147): Vorher zaehlten nur /app und /admin —
 * auf /abo wurde der Token zwar geloescht, aber weder Grund gemerkt noch
 * umgeleitet. Die Bereichszuordnung kommt jetzt aus lib/rollen (deckt /abo ab).
 */
export function imGeschuetztenBereich(pfad) {
  return [BEREICH_FIRMA, BEREICH_ADMIN].includes(bereichVonPfad(pfad));
}

/**
 * Adresse der Anmeldung nach einer beendeten Sitzung — MIT Rueckweg.
 *
 * Pruefbericht 20.09.2026 (U-151): Vorher nur "/login?reason=session"; nach
 * der neuen Anmeldung landete man auf der Startseite statt dort, wo man war.
 * Login.jsx prueft `next` ueber sicheresZiel (nur eigene, passende Ziele).
 */
export function anmeldeAdresse(loc = window.location) {
  const ziel = `${loc.pathname || ""}${loc.search || ""}${loc.hash || ""}`;
  return `/login?reason=session&next=${encodeURIComponent(ziel)}`;
}

/**
 * Zur Anmeldung umleiten, weil die Sitzung beendet ist.
 *
 * Pruefbericht 20.09.2026 (U-80): Solange eine Stelle "ungespeichert"
 * meldete, fragte der Browser vor dieser Umleitung nach — wer "bleiben"
 * waehlte, stand ohne Token da. Die Sitzung ist weg und der Entwurf liegt
 * gesichert (RP-412), also die Rueckfrage aufheben und dann umleiten.
 */
function zurAnmeldung() {
  ungespeichertVerwerfen();
  window.location.href = anmeldeAdresse(window.location);
}

export const ABMELDEGRUND_SCHLUESSEL = "ah_abmeldegrund";

/**
 * Gemerkter Abmeldegrund dieses Tabs ("" = keiner). Wirft nie.
 *
 * Welle A9 (22.09.2026): Nach einer Firmensperre (403, X-Sperre: firma) kam
 * der Servertext nie an. Der Abfaenger merkt den Grund und setzt die harte
 * Umleitung /login?reason=session — vorher rendert React aber schon
 * ProtectedRoute mit user=null, also <Navigate to="/login?next=…">, und
 * Login.jsx las UND loeschte den Grund einmalig. Die harte Umleitung fand
 * dann nur noch den allgemeinen Satz vor. Deshalb: lesen ohne loeschen,
 * vergessen erst beim Verlassen der Anmeldeseite (abmeldegrundVergessen).
 */
export function abmeldegrundLesen() {
  try {
    const g = sitzungsSpeicher()?.getItem(ABMELDEGRUND_SCHLUESSEL);
    return typeof g === "string" ? g : "";
  } catch { return ""; }
}

/** Gemerkten Abmeldegrund verwerfen (nach der Anzeige). Wirft nie. */
export function abmeldegrundVergessen() {
  try { sitzungsSpeicher()?.removeItem(ABMELDEGRUND_SCHLUESSEL); } catch { /* gesperrter Speicher */ }
}

/**
 * Abmeldegrund fuer die Anmeldeseite merken (nur dieser Tab, nie in der URL)
 * und den zuletzt angezeigten Vergleich verwerfen — beides darf nie werfen.
 */
export function abmeldegrundMerken(detail) {
  const speicher = sitzungsSpeicher();
  schreiben(speicher, ABMELDEGRUND_SCHLUESSEL, typeof detail === "string" && detail ? detail : "");
  // Runde 27 (Pruefbefund P0): Auch der zuletzt angezeigte Vergleich muss
  // weg — sonst sieht der naechste Nutzer an diesem Browser Fahrzeug,
  // Verkaeuferdaten und Vertrag des vorherigen Kontos.
  try { vergleichLeeren(speicher); } catch { /* gesperrter Speicher */ }
}

/**
 * Fehlerantworten auf Datei-Abrufe (responseType "blob") kommen als Blob an —
 * errMsg fand darin keinen Text und zeigte "Request failed with status code
 * 400" statt der deutschen Meldung des Servers (Pruefbericht 20.09.2026, M34).
 * Hier wird ein JSON-Blob in ein normales Objekt zurueckverwandelt.
 */
export async function blobFehlerLesbar(err) {
  const d = err?.response?.data;
  if (typeof Blob === "undefined" || !(d instanceof Blob)) return err;
  if (!/json/i.test(d.type || "")) return err;
  try {
    err.response.data = JSON.parse(await blobAlsText(d));
  } catch { /* kein JSON — dann bleibt es bei der allgemeinen Meldung */ }
  return err;
}

// Blob.text() fehlt in aelteren Browsern (Safari < 14) — dann FileReader.
function blobAlsText(blob) {
  if (typeof blob.text === "function") return blob.text();
  return new Promise((res, rej) => {
    const leser = new FileReader();
    leser.onload = () => res(String(leser.result || ""));
    leser.onerror = () => rej(leser.error);
    leser.readAsText(blob);
  });
}

/**
 * Pruefbericht 20.09.2026 (U-143/H2): Lief das persoenliche Abo mitten in der
 * Sitzung ab, blieb der Kontext auf "aktiv" — die Routensperre griff nie,
 * jeder Klick brachte nur einen Drei-Wort-Toast. Der AuthProvider meldet hier
 * seinen refresh() an; bei jeder 402 wird der Abo-Stand neu geladen (danach
 * leitet ProtectedRoute die abo-pflichtigen Seiten selbst auf /abo um).
 */
let _aboNeuLaden = null;
let _aboZuletzt = 0;
export function aboNeuLadenAnmelden(fn) { _aboNeuLaden = fn; }

/** Höchstens so viele automatische Wiederholungen je Anfrage. */
export const WIEDERHOLEN_MAX = 2;

/**
 * Darf diese gescheiterte Anfrage automatisch wiederholt werden?
 *
 * Prüfbericht 20.09.2026 (P1): Legen zwei Sucher fast gleichzeitig einen
 * Vertrag zum SELBEN Fahrzeug an, wartet der zweite serverseitig 6 s und
 * bekam dann einen sichtbaren Fehler — er musste von Hand noch einmal
 * speichern. Der Server weiß an dieser Stelle aber, dass NICHTS
 * geschrieben wurde, und sagt es mit `X-Wiederholen: 1`.
 *
 * Wichtig: NUR bei dieser Kopfzeile. Ein beliebiger 503 darf niemals
 * wiederholt werden — beim Anlegen eines Vertrags entstünde sonst ein
 * zweiter. (Rein exportiert, damit es sich prüfen lässt.)
 */
export function darfWiederholen(err) {
  const r = err?.response;
  if (!r || r.status !== 503) return false;
  const kopf = r.headers?.["x-wiederholen"] ?? r.headers?.["X-Wiederholen"];
  if (String(kopf || "") !== "1") return false;
  return (err.config?.__versuche || 0) < WIEDERHOLEN_MAX;
}

/** Wartezeit bis zum nächsten Versuch (Sekunden aus Retry-After, sonst 3 s). */
export function wiederholenNachMs(err) {
  const roh = err?.response?.headers?.["retry-after"];
  const sek = Number.parseInt(String(roh ?? ""), 10);
  return (Number.isFinite(sek) && sek > 0 ? Math.min(sek, 30) : 3) * 1000;
}

/**
 * Rollenprüfung 22.09.2026 (RP-546): gleitende Sitzung. Bringt eine
 * erfolgreiche Antwort die Kopfzeile `X-Neues-Token` mit (Restlaufzeit unter
 * 2 Tagen, backend/auth.token_erneuern), wird das neue Token abgelegt — aber
 * nur, wenn die Anfrage mit dem Token gesendet wurde, das GERADE gilt. Eine
 * verspätete Antwort einer früheren Anmeldung (oder eine Anfrage mit fremdem
 * Token, z. B. Käufer/Fahrer) darf die aktuelle Anmeldung nie ersetzen
 * (gleiche Regel wie gehoertZumAktuellenToken, Nr. 39/40).
 * Rein exportiert (mit `key`), damit Käufer- und Fahrer-Instanzen dieselbe
 * Regel nutzen können und es sich prüfen lässt. Wirft nie.
 */
export function neuesTokenUebernehmen(r, key = TOKEN_APP) {
  try {
    const h = r?.headers;
    const kopf = (typeof h?.get === "function" ? h.get("x-neues-token") : null)
      ?? h?.["x-neues-token"] ?? h?.["X-Neues-Token"];
    if (!kopf) return false;
    const aktuell = tokenLesen(key);
    if (!aktuell) return false;
    const gesendet = String(r?.config?.headers?.Authorization || "");
    if (gesendet !== `Bearer ${aktuell}`) return false;
    return tokenErneuern(key, aktuell, String(kopf));
  } catch {
    return false;
  }
}

api.interceptors.response.use(
  (r) => { neuesTokenUebernehmen(r, TOKEN_APP); return r; },
  async (err) => {
    await blobFehlerLesbar(err);
    if (darfWiederholen(err)) {
      const config = err.config;
      config.__versuche = (config.__versuche || 0) + 1;
      return new Promise((res) => setTimeout(res, wiederholenNachMs(err)))
        .then(() => api(config));
    }
    if (err?.response?.status === 402 && _aboNeuLaden && Date.now() - _aboZuletzt > 5000) {
      _aboZuletzt = Date.now();
      try { _aboNeuLaden(); } catch { /* egal */ }
    }
    if (istFirmensperre(err)) {
      // Pruefbericht 20.09.2026 (B2/H1): Firma waehrend der Arbeit gesperrt.
      // Vorher lieferte jeder Endpunkt ein stummes 403, Seiten mit
      // verschluckten Fehlern blieben leer. Jetzt: abmelden wie bei 401, mit
      // dem Text des Servers als Begruendung auf der Anmeldeseite.
      if (gehoertZumAktuellenToken(err?.config, tokenLesen(TOKEN_APP)) && tokenLesen(TOKEN_APP)) {
        tokenLoeschen(TOKEN_APP);
        abmeldegrundMerken(err?.response?.data?.detail);
        // U-147: ueber die Bereichszuordnung (deckt /abo ab), U-80/U-151: ohne
        // Rueckfrage und mit Rueckweg.
        if (imGeschuetztenBereich(window.location.pathname)) zurAnmeldung();
      }
      return Promise.reject(err);
    }
    if (err?.response?.status === 401) {
      const url = err?.config?.url || "";
      if (!gehoertZumAktuellenToken(err?.config, tokenLesen(TOKEN_APP))) {
        // Veraltete Antwort einer frueheren Anmeldung — die neue Sitzung
        // bleibt bestehen (Nr. 39/40).
        return Promise.reject(err);
      }
      if (loestAbmeldungAus(url)) {
        tokenLoeschen(TOKEN_APP);
        const pfad = window.location.pathname;
        // U-147: /app, /admin UND /abo (lib/rollen entscheidet).
        const imBereich = imGeschuetztenBereich(pfad);
        // /start (Einstieg der installierten App, 09/2026) leitet selbst
        // weiter — den Grund trotzdem merken, damit die Anmeldung ihn zeigt.
        if (imBereich || pfad === "/start") {
          // Runde 19: den ECHTEN Grund mitnehmen (neue Anmeldung wann/wo,
          // Abmeldung, Sperre, abgelaufen) — vorher stand fuer jede 401
          // "auf einem anderen Geraet verwendet". Nicht in die URL (Verlauf,
          // Logs), sondern nur fuer diesen Tab.
          abmeldegrundMerken(err?.response?.data?.detail);
        }
        // U-80/U-151: ohne Rueckfrage, mit Rueckweg (next=).
        if (imBereich) zurAnmeldung();
      }
    }
    return Promise.reject(err);
  }
);

// Geschuetzte Datei (PDF/PNG) in neuem Tab oeffnen — Abruf per
// Authorization-Header statt ?auth=<token> in der URL (der Token landete
// sonst in Browser-Verlauf, Proxy- und Server-Logs).
export async function openAuthedFile(path, mime = "application/pdf", client = api) {
  // Datei erst laden, dann per unsichtbarem <a target="_blank">-Klick
  // oeffnen — dasselbe Muster wie beim Kaufvertrag-PDF (openContractPdf).
  // Der fruehere Weg (leeren Tab synchron oeffnen, opener kappen, Blob-URL
  // nachtragen) bleibt in neueren Chrome-Versionen dauerhaft weiss: nach
  // `opener = null` liegt der Tab in einer eigenen Storage-Partition und
  // darf die Blob-URL des Ursprungs-Tabs nicht mehr laden.
  // Pruefbericht 20.09.2026 (B15): dauert das Laden laenger, als der Browser
  // den Klick gelten laesst, kommt ein Hinweis mit Knopf (lib/dateiOeffnen).
  const startMs = Date.now();
  const res = await client.get(path, { responseType: "blob" });
  return blobOeffnen(res.data, {
    startMs, mime, titel: String(mime).startsWith("image/") ? "Das Foto" : "Das Dokument",
  });
}

/**
 * Normalize a FastAPI / axios error into a human-readable string.
 *
 * FastAPI 422 returns `{ detail: [{type, loc, msg, input, url}, ...] }` —
 * which can't be rendered as a React child. This helper flattens any
 * shape (string, array of pydantic errors, single object, …) into one
 * line so it's safe to pass to `toast.error(...)` or JSX.
 */
export const errMsg = (err, fallback = "Ein Fehler ist aufgetreten") => {
  // Pruefbericht 20.09.2026 (U-08/U-29/M9): Ohne Serverantwort kamen die
  // englischen axios-Texte durch ("timeout of 60000ms exceeded",
  // "Network Error"). Jetzt deutsch und mit Handlungsanweisung.
  if (err && !err.response) {
    const code = String(err.code || "");
    if (code === "ECONNABORTED" || code === "ETIMEDOUT" || /timeout/i.test(String(err.message || ""))) {
      // DP-04: Beim Fahrzeug-Abruf laeuft der Server (Apify) im Hintergrund
      // weiter — der zweite Versuch in einer Minute trifft meist den Cache.
      if (istAbruf(err.config)) return ABRUF_ZEITUEBERSCHREITUNG;
      return "Der Server hat nicht rechtzeitig geantwortet — bitte gleich noch einmal versuchen.";
    }
    if (code === "ERR_NETWORK" || String(err.message || "") === "Network Error") {
      return "Keine Verbindung zum Server — bitte Internetverbindung prüfen und erneut versuchen.";
    }
  }
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    const parts = d.map(validierungsText);
    return parts.filter(Boolean).join(" · ") || fallback;
  }
  if (d && typeof d === "object") return validierungsText(d) || fallback;
  return err?.message || fallback;
};

// Rollenprüfung 22.09.2026 (RP-036/RP-286): deutsche Namen der Felder, die in
// Längenfehlern am häufigsten vorkommen. Unbekannte Felder bleiben, wie sie sind.
const FELDNAMEN = {
  description: "Beschreibung", title: "Titel", notes: "Notizen", notiz: "Notiz",
  location: "Standort", label: "Bezeichnung", costs: "Kosten", features: "Ausstattung",
  known_defects: "Bekannte Mängel", message: "Nachricht", nachricht: "Nachricht",
  grund: "Grund", name: "Name", company_name: "Firmenname", contact_person: "Ansprechpartner",
  email: "E-Mail", phone: "Telefon", address: "Adresse", city: "Ort", zip_code: "PLZ",
  default_terms: "Vertragsbedingungen", agb: "AGB",
};

/**
 * Ein Eintrag einer FastAPI-422-Liste als deutscher Satz.
 *
 * Rollenprüfung 22.09.2026 (RP-036/RP-286): Längenfehler kamen roh als
 * englische Pydantic-Meldung an ("String should have at most 500 characters").
 * Jetzt: "Beschreibung: höchstens 500 Zeichen". Das Feld ist der letzte
 * Name in `loc` (Listen-Indizes und "body" zählen nicht). Alles andere wie
 * bisher über `msg`. Rein exportiert, damit es sich prüfen lässt.
 */
export function validierungsText(it) {
  if (typeof it === "string") return it;
  if (!it || typeof it !== "object") return "";
  const typ = String(it.type || "");
  const loc = Array.isArray(it.loc) ? it.loc : [];
  const roh = [...loc].reverse().find(
    (t) => typeof t === "string" && !["body", "query", "path"].includes(t));
  const feld = roh ? (FELDNAMEN[roh] || roh) : "";
  const mit = (satz) => (feld ? `${feld}: ${satz}` : satz.charAt(0).toUpperCase() + satz.slice(1));
  const max = it.ctx?.max_length;
  const min = it.ctx?.min_length;
  if (typ === "string_too_long" && max != null) return mit(`höchstens ${max} Zeichen`);
  if (typ === "too_long" && max != null) return mit(`höchstens ${max} Einträge`);
  if (typ === "string_too_short" && min != null) {
    return Number(min) <= 1 ? mit("darf nicht leer sein") : mit(`mindestens ${min} Zeichen`);
  }
  // Gleicher Wortlaut wie backend/server.py _meldung_deutsch (Listen).
  if (typ === "too_short" && min != null) {
    return Number(min) <= 1 ? mit("mindestens ein Eintrag") : mit(`mindestens ${min} Einträge`);
  }
  return String(it.msg || JSON.stringify(it)).replace(/^Value error, /, "");
}
