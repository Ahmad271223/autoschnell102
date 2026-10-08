// AutoSchnell Helfer — Hintergrund (Service Worker).
//
// 1. Abruf-Helfer (seit 08/2026): holt fuer die AutoSchnell-App Kleinanzeigen-Seiten ueber die Leitung
//    des Nutzers (AUTOSCHNELL_FETCH, content.js) — unveraendert.
// 2. Browser-Helfer (04.10.2026): Inserat geoeffnet -> Seite an AutoSchnell -> Vergleiche mit den
//    Firmenregeln oeffnen sich im Hintergrund, die Vergleichsseite kommt zurueck (Platz + Ampel), die
//    Box im Inserat zeigt alles, "Kaufvertrag" oeffnet das Auto sofort in AutoSchnell.
//    Verbunden wird mit dem 6-stelligen Code aus AutoSchnell (Programme) — der Schluessel kann nur
//    diese Erweiterung bedienen, keine Anmeldung, kein Passwort. Ein Konto = ein Browser.
// 3. Zusammen mit dem Windows-Programm (2.3.0): was das Programm gerade verglichen hat, oeffnet der Helfer nicht
//    doppelt; die Vergleichsseiten des Programms bekommen die Ampel (programm-suche -> marktlage).

const VERSION = chrome.runtime.getManifest().version;
const SERVER_STANDARD = "https://app.auto-schnellkauf.de";
const WERKZEUG = "browser-helfer";
const WIEDERHOLEN_MS = 30 * 60 * 1000;     // dasselbe Inserat innerhalb 30 min: nichts neu oeffnen
const ANFRAGE_MS = 25000;

// ------------------------------------------------------------------ 1. Abruf-Helfer (unveraendert)
// 2.6.3 (Pruefung 05.10.2026, Paket 2): Adresse als URL pruefen (nicht als Text — "/s-anzeige/../x" fuehrte woanders
// hin), Zeitgrenze (die App gibt nach 25 s auf, der Abruf lief weiter) und hoechstens 3 gleichzeitig (sonst konnte
// ein Skript die Leitung des Nutzers bei Kleinanzeigen bis zur Sperre auslasten).
const ABRUF_MS = 20000;
const ABRUF_GLEICHZEITIG = 3;
let abrufeLaufend = 0;

function kleinanzeigenInserat(url) {
  try {
    const u = new URL(String(url || ""));
    return u.protocol === "https:" && /^(www\.)?kleinanzeigen\.de$/.test(u.hostname)
      && u.pathname.startsWith("/s-anzeige/") && !u.pathname.includes("/../") ? u.href : null;
  } catch (e) {
    return null;
  }
}

function abrufHelfer(msg, sendResponse) {
  const url = kleinanzeigenInserat(msg.url);
  if (!url) {
    sendResponse({ ok: false, error: "Nur Kleinanzeigen-Fahrzeuglinks erlaubt." });
    return;
  }
  if (abrufeLaufend >= ABRUF_GLEICHZEITIG) {
    sendResponse({ ok: false, error: "Gerade laufen schon mehrere Abrufe – bitte gleich noch einmal." });
    return;
  }
  abrufeLaufend++;
  const abbruch = new AbortController();
  const uhr = setTimeout(() => abbruch.abort(), ABRUF_MS);
  fetch(url, { credentials: "omit", headers: { Accept: "text/html,application/xhtml+xml" }, signal: abbruch.signal })
    .then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text();
    })
    .then((html) => sendResponse({ ok: true, html }))
    .catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }))
    .finally(() => { clearTimeout(uhr); abrufeLaufend--; });
}

// ------------------------------------------------------------------ Verbindung + Server
/** Erlaubte Server: die Live-Adresse; zum Testen localhost/127.0.0.1 (Recht wird dann erfragt). */
function serverErlaubt(s) {
  return s === SERVER_STANDARD || /^http:\/\/(localhost|127\.0\.0\.1)(:\d{2,5})?$/.test(s);
}

/** 2.6.0 (Pruefung 05.10.2026, Nr. 12): Den Kleinanzeigen-Abruf darf nur AutoSchnell selbst ausloesen — nicht jede
 *  Seite, auf der content.js laeuft (z. B. die Beispiel-Adresse autoschnell.de). */
function appHerkunft(sender) {
  let herkunft = "";
  try { herkunft = new URL(String(sender.url || "")).origin; } catch (e) { return false; }
  return !!sender.tab && (serverErlaubt(herkunft)
    || ["https://auto-schnellkauf.de", "https://www.auto-schnellkauf.de"].includes(herkunft));
}

/** 2.6.0 (Nr. 7): Gemerkte Inserate gehoeren zur Verbindung — beim Trennen/Neuverbinden weg damit (sonst 30 min
 *  alte Antworten mit Links und Vergleichs-IDs eines anderen Kontos). */
async function sitzungLeeren() {
  try { await chrome.storage.session.clear(); } catch (e) { /* egal */ }
  zuletztAutomatisch.clear();
}

async function lokal(schluessel) {
  return chrome.storage.local.get(schluessel);
}

// 2.6.3 (Paket 2): Ohne Abo (402), gesperrt (403) oder ohne Netz (0) lud der Helfer trotzdem jede Inseratsseite
// hoch (200–300 KB), nur um dieselbe Antwort zu bekommen — jetzt merkt er sich die Sperre kurz und antwortet
// sofort aus dem Gedaechtnis. Ein 200 loescht den Merker.
const SPERRE_MS = { 402: 5 * 60000, 403: 5 * 60000, 0: 60000 };

async function sperreLesen() {
  try {
    const { sperre } = await chrome.storage.session.get("sperre");
    return sperre && sperre.bis > Date.now() ? sperre : null;
  } catch (e) { return null; }
}

async function sperreMerken(status, text) {
  // Ein Normal-Abo darf Pro nicht benutzen. Diese 403 aber nur kurz merken:
  // wird das Konto im Admin auf Pro hochgestuft, soll ein bereits installierter
  // Helfer praktisch sofort wieder arbeiten und nicht 5 Minuten am alten
  // Sperrstand haengen. Andere 403 bleiben wie bisher 5 Minuten gecacht.
  const proNur = status === 403 && /AutoSchnell Pro/i.test(String(text || ""));
  const dauer = proNur ? 15000 : SPERRE_MS[status];
  try {
    if (dauer) await chrome.storage.session.set({ sperre: { status, text, bis: Date.now() + dauer } });
    else if (status === 200) await chrome.storage.session.remove("sperre");
  } catch (e) { /* egal */ }
}

async function server() {
  const { server: s } = await lokal("server");
  return s && serverErlaubt(s) ? s : SERVER_STANDARD;
}

async function geraetKennung() {
  let { geraet } = await lokal("geraet");
  if (!geraet) {
    geraet = crypto.randomUUID();
    await chrome.storage.local.set({ geraet });
  }
  return geraet;
}

function browserName() {
  const marken = ((navigator.userAgentData && navigator.userAgentData.brands) || []).map((b) => b.brand);
  const name = marken.find((b) => /edge/i.test(b)) ? "Edge" : marken.find((b) => /chrome/i.test(b)) ? "Chrome" : "Browser";
  const os = (navigator.userAgentData && navigator.userAgentData.platform) || "";
  return (name + (os ? " · " + os : "")).slice(0, 80);
}

async function api(pfad, { methode = "GET", daten, ohneSchluessel = false } = {}) {
  // Tempo (2.4.0): Server und Schluessel in EINEM Speicherzugriff
  const gespeichert = await lokal(["server", "schluessel"]);
  const basis = gespeichert.server && serverErlaubt(gespeichert.server) ? gespeichert.server : SERVER_STANDARD;
  const schluessel = gespeichert.schluessel;
  const kopf = { "Content-Type": "application/json", "X-Werkzeug-Version": VERSION };
  if (!ohneSchluessel && schluessel) kopf["X-Werkzeug-Schluessel"] = schluessel;
  const abbruch = new AbortController();
  const uhr = setTimeout(() => abbruch.abort(), ANFRAGE_MS);
  try {
    const r = await fetch(basis + "/api" + pfad, {
      method: methode, headers: kopf, credentials: "omit", cache: "no-store",
      body: daten === undefined ? undefined : JSON.stringify(daten), signal: abbruch.signal,
    });
    let antwort = null;
    try { antwort = await r.json(); } catch (e) { antwort = null; }
    if (!ohneSchluessel && (r.status === 200 || r.status === 402 || r.status === 403)) {
      await sperreMerken(r.status, fehlertext(r.status, antwort, "Gesperrt."));
    }
    if (r.status === 401 && !ohneSchluessel) {
      // Verbindung gilt nicht mehr (anderer Browser, Chef, App) — der Server sagt genau, warum.
      // 2.6.0 (Nr. 8): nur, wenn noch DERSELBE Schluessel gespeichert ist — eine alte Anfrage, die nach dem
      // Neu-Verbinden zurueckkommt, darf den neuen Schluessel nicht loeschen.
      const { schluessel: jetzt } = await lokal("schluessel");
      if (jetzt && jetzt === schluessel) {
        await chrome.storage.local.remove(["schluessel"]);
        await chrome.storage.local.set({ getrenntGrund: (antwort && antwort.detail) || "Nicht mehr verbunden." });
        await sitzungLeeren();
      }
    }
    return { status: r.status, daten: antwort };
  } catch (e) {
    if (!ohneSchluessel) await sperreMerken(0, "AutoSchnell ist gerade nicht erreichbar – Internetverbindung prüfen.");
    return { status: 0, daten: { detail: "AutoSchnell ist gerade nicht erreichbar – Internetverbindung prüfen." } };
  } finally {
    clearTimeout(uhr);
  }
}

function fehlertext(status, daten, ersatz) {
  const d = daten && daten.detail;
  if (typeof d === "string" && d) return d;
  if (status === 402) return "Kein aktives AutoSchnell-Abo.";
  return ersatz;
}

// ------------------------------------------------------------------ Sitzungsspeicher (ueberlebt Pausen des Service Workers)
async function sitzung() {
  const s = await chrome.storage.session.get(["inserate", "vergleichsTabs", "programmTabs"]);
  return { inserate: s.inserate || {}, vergleichsTabs: s.vergleichsTabs || {}, programmTabs: s.programmTabs || {} };
}

let sperre = Promise.resolve();
/** Lesen-Aendern-Schreiben streng nacheinander (gleichzeitige Tabs ueberschreiben sich sonst). */
function sitzungAendern(fn) {
  const lauf = sperre.then(async () => {
    const s = await sitzung();
    const ergebnis = await fn(s);
    const jetzt = Date.now();
    for (const [k, v] of Object.entries(s.inserate)) if (jetzt - v.zeit > 2 * 60 * 60 * 1000) delete s.inserate[k];
    for (const [k, v] of Object.entries(s.programmTabs)) if (jetzt - (v.zeit || 0) > 2 * 60 * 60 * 1000) delete s.programmTabs[k];
    await chrome.storage.session.set(s);
    return ergebnis;
  });
  sperre = lauf.catch(() => null);
  return lauf;
}

// Wunsch Ahmad 06.10.2026: wie im Windows-Programm waehlen, ob mobile.de, AutoScout24 oder beide aufgehen
// (vorher immer beide). Die Wahl haelt der Hintergrund auch im Speicher (erlaubteLinks ist synchron).
let portalWahl = { mobile: true, autoscout: true };
const portalAn = (portal) => (portal === "AutoScout24" ? portalWahl.autoscout : portalWahl.mobile) !== false;

async function einstellungen() {
  const { einstellungen: e } = await lokal("einstellungen");
  const alle = { vergleicheOeffnen: true, mobile: true, autoscout: true, ...(e || {}) };
  portalWahl = { mobile: alle.mobile !== false, autoscout: alle.autoscout !== false };
  return alle;
}
einstellungen().catch(() => {});

// ------------------------------------------------------------------ Inserat
/** 2.6.0 (Pruefung 05.10.2026, Nr. 22): nur Suchseiten von mobile.de und AutoScout24 oeffnen bzw. mit den Cookies
 *  des Nutzers abrufen — egal, was der Server schickt. */
function erlaubterLink(url) {
  try {
    const u = new URL(String(url || ""));
    if (u.protocol !== "https:") return false;
    if (u.hostname === "suchen.mobile.de") return u.pathname.startsWith("/fahrzeuge/search.html");
    return /^www\.autoscout24\.(de|at|ch)$/.test(u.hostname) && u.pathname.startsWith("/lst");
  } catch (e) {
    return false;
  }
}
const erlaubteLinks = (antwort) =>
  ((antwort && antwort.links) || []).filter((l) => l && erlaubterLink(l.url) && portalAn(l.portal));

async function vergleicheOeffnen(tab, kennung, antwort) {
  const links = erlaubteLinks(antwort);
  const s = await sitzung();
  const neue = [];
  const abgehaengt = [];
  for (let i = 0; i < links.length; i++) {
    const eintrag = { vergleich_id: antwort.vergleich_id, inseratTab: tab.id, kennung, portal: links[i].portal };
    // 2.6.0 (Nr. 2): den Vergleichs-Tab dieses Inserat-Tabs fuer dasselbe Portal wiederverwenden ("naechstes
    // Fahrzeug" ohne Neuladen, erneuter Knopfdruck) — statt bei jedem Auto zwei neue Tabs
    const alt = Object.entries(s.vergleichsTabs).find(([, v]) => v.inseratTab === tab.id && v.portal === links[i].portal);
    if (alt) {
      try {
        // 2.6.2 (Pruefung 05.10.2026, Paket 1): nur, wenn dort noch eine Vergleichsseite steht. Hat der Nutzer in
        // dem Tab ein Auto geoeffnet (vielleicht schon das Kontaktformular ausgefuellt) oder ist ganz woanders,
        // bleibt der Tab wie er ist — die Vergleiche gehen in einen neuen.
        const altTab = await chrome.tabs.get(Number(alt[0]));
        if (erlaubterLink(altTab.pendingUrl || altTab.url || "")) {
          await chrome.tabs.update(Number(alt[0]), { url: links[i].url });
          neue.push([Number(alt[0]), eintrag]);
          continue;
        }
        abgehaengt.push(Number(alt[0]));
      } catch (e) { /* Tab inzwischen zu: neu anlegen */ }
    }
    // im Fenster des Inserats (Nr. 16); App-/Popup-Fenster haben keine Tabs -> dann im normalen Fenster
    for (const ort of [{ windowId: tab.windowId, index: tab.index + 1 + i, openerTabId: tab.id }, {}]) {
      try {
        const t = await chrome.tabs.create({ url: links[i].url, active: false, ...ort });
        neue.push([t.id, eintrag]);
        break;
      } catch (e) { /* naechster Versuch */ }
    }
  }
  await sitzungAendern((x) => {
    // der Tab bleibt "aus einem Vergleich" (Autos darin oeffnen nichts von selbst), wird aber nie mehr ueberschrieben
    for (const id of abgehaengt) if (x.vergleichsTabs[id]) Object.assign(x.vergleichsTabs[id], { inseratTab: -1, erledigt: true });
    for (const [id, eintrag] of neue) x.vergleichsTabs[id] = eintrag;      // ohne "erledigt": wird neu ausgewertet
    if (x.inserate[kennung]) x.inserate[kennung].geoeffnet = true;
  });
  return neue.length;
}

// 2.6.2 (Pruefung 05.10.2026, Paket 1): Doppelklick auf "Vergleich öffnen" (oder Knopf + Automatik im selben
// Augenblick) oeffnete die Tabs doppelt — der zweite Start las die Tab-Liste, bevor der erste sie geschrieben hatte.
const DOPPELT_MS = 1500;
const zuletztGestartet = new Map();

/** Tempo: Vergleichsseiten sofort direkt holen und die Tabs anlegen — ohne dass die Box darauf wartet. */
function vergleicheStarten(tab, kennung, antwort) {
  const jetzt = Date.now();
  if (jetzt - (zuletztGestartet.get(kennung) || 0) < DOPPELT_MS) return erlaubteLinks(antwort).length;
  zuletztGestartet.set(kennung, jetzt);
  for (const [k, z] of zuletztGestartet) if (jetzt - z > 60000) zuletztGestartet.delete(k);
  zuletztAutomatisch.set(kennung, Date.now());
  // sofort vermerken (Neuladen des Inserats oeffnet nichts doppelt), die Tabs folgen gleich
  sitzungAendern((s) => {
    if (s.inserate[kennung]) Object.assign(s.inserate[kennung], { geoeffnet: true, lageFehler: {} });   // neuer Versuch
  }).catch(() => {});
  direktAuswerten(kennung, antwort, tab.id).catch(() => {});
  vergleicheOeffnen(tab, kennung, antwort).catch(() => {});
  return erlaubteLinks(antwort).length;
}

// 2.6.0 (Pruefung 05.10.2026, Nr. 2/13): Bremse gegen eine Tab-Flut. Von selbst oeffnen nur
//   - wenn man das Inserat wirklich ansieht (sichtbarer Tab; ein im Hintergrund geoeffnetes Inserat oeffnet seine
//     Vergleiche erst, wenn man hinwechselt — Mittelklick auf 10 Treffer = keine 20 Tabs auf einmal),
//   - nicht nach Neuladen, Zurueck/Vor oder bei wiederhergestellten/verworfenen Tabs (Browser-Neustart),
//   - hoechstens AUTO_JE_MINUTE Inserate je Minute, und dasselbe Inserat nie zweimal gleichzeitig.
const AUTO_JE_MINUTE = 8;
const zuletztAutomatisch = new Map();   // kennung -> Zeit (Speicher des Service Workers; "geoeffnet" steht in der Sitzung)
const autoZeiten = [];
const laufendeInserate = new Map();     // kennung -> laufendes POST /inserat (F5 waehrend des Hochladens, zwei Tabs)

function automatischErlaubt(kennung) {
  const jetzt = Date.now();
  const zuletzt = zuletztAutomatisch.get(kennung);
  if (zuletzt && jetzt - zuletzt < WIEDERHOLEN_MS) return "schon_offen";
  while (autoZeiten.length && jetzt - autoZeiten[0] > 60000) autoZeiten.shift();
  if (autoZeiten.length >= AUTO_JE_MINUTE) return "gebremst";
  autoZeiten.push(jetzt);
  zuletztAutomatisch.set(kennung, jetzt);
  return "";
}

/** Warum (nicht) von selbst geoeffnet wird — "" = jetzt oeffnen. Die Box zeigt den Grund. */
function automatik({ e, msg, ausVergleich, schonOffen, vomProgramm }) {
  if (msg.ohneOeffnen) return "nachgelesen";
  if (!e.vergleicheOeffnen) return "aus";
  if (ausVergleich) return "aus_vergleich";
  if (schonOffen) return "schon_offen";
  if (vomProgramm) return "programm";
  const a = msg.ansicht || {};                     // aeltere Seiten-Skripte schicken das nicht: wie bisher
  if (a.verworfen || a.navTyp === "reload" || a.navTyp === "back_forward") return "neu_geladen";
  if (a.sichtbar === false) return "hintergrund";
  return "";
}

// Tempo (2.4.0): gemeinsam.js meldet ein Inserat schon beim Seitenstart. Der Hintergrund ist damit wach, und eine
// kleine Anfrage baut die Verbindung zu AutoSchnell auf (Namensaufloesung, TLS) — wenn portal.js die Seite schickt,
// steht beides schon. Hoechstens alle VORWAERMEN_MS eine Anfrage.
const VORWAERMEN_MS = 15000;
let vorgewaermt = 0;

// 2.6.0 (Pruefung 05.10.2026, Nr. 9/15): /status hoechstens alle 30 min — gibt es eine neuere Version zum
// Herunterladen (entpackte Erweiterungen aktualisieren sich nicht selbst)? Hat das Konto das Windows-Programm
// (sonst fragt jede Ergebnisseite umsonst /programm-suche)?
const STATUS_MS = 30 * 60 * 1000;

async function statusHolen() {
  const { statusMerker } = await lokal("statusMerker");
  if (statusMerker && Date.now() - statusMerker.zeit < STATUS_MS) return statusMerker;
  const r = await api(`/werkzeuge/${WERKZEUG}/status`);
  if (r.status !== 200 || !r.daten) return statusMerker || null;
  const merker = { zeit: Date.now(), aktuelle_version: r.daten.aktuelle_version || "",
                   programm_verbunden: r.daten.programm_verbunden !== false };
  await chrome.storage.local.set({ statusMerker: merker });
  return merker;
}

/** Ist Version a neuer als b ("2.10.0" > "2.9.1")? */
function istNeuer(a, b) {
  const t = (v) => String(v || "").split(".").map((x) => parseInt(x, 10) || 0);
  const x = t(a);
  const y = t(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
  }
  return false;
}

async function fruehBearbeiten() {
  const gespeichert = await lokal(["server", "schluessel", "statusMerker"]);
  if (!gespeichert.schluessel) return { ok: false };
  const m = gespeichert.statusMerker;
  if (!m || Date.now() - m.zeit >= STATUS_MS) {
    vorgewaermt = Date.now();
    statusHolen().catch(() => {});                   // baut dabei auch die Verbindung auf
  } else if (Date.now() - vorgewaermt > VORWAERMEN_MS) {
    vorgewaermt = Date.now();
    const basis = gespeichert.server && serverErlaubt(gespeichert.server) ? gespeichert.server : SERVER_STANDARD;
    fetch(basis + "/api/health", { credentials: "omit", cache: "no-store" }).catch(() => {});
  }
  return { ok: true };
}

async function inseratBearbeiten(msg, tab) {
  const kennung = String(msg.kennung || "");
  const [{ schluessel, getrenntGrund, statusMerker }, s, e] = await Promise.all([
    lokal(["schluessel", "getrenntGrund", "statusMerker"]), sitzung(), einstellungen()]);
  if (!schluessel) {
    return { fehler: "nicht_verbunden", text: getrenntGrund || "Nicht verbunden – auf das AutoSchnell-Symbol klicken und den Code aus AutoSchnell eintippen." };
  }
  const sperre = await sperreLesen();
  if (sperre && !(s.inserate[kennung] || {}).antwort) return { fehler: "server", status: sperre.status, text: sperre.text };
  const vorher = s.inserate[kennung];
  // Aus einer unserer Vergleichsseiten geoeffnet (neuer Tab) oder darin weitergeklickt (derselbe Tab)?
  // Dann nichts automatisch oeffnen — sonst oeffnet jedes angeschaute Vergleichsauto neue Vergleiche.
  // Seit 2.3.0 zaehlen auch die Vergleichsseiten des Windows-Programms dazu (programmTabs).
  const vergleichsTab = (id) => !!(id && (s.vergleichsTabs[id] || s.programmTabs[id]));
  const ausVergleich = vergleichsTab(tab.openerTabId) || vergleichsTab(tab.id);
  let antwort;
  if (vorher && Date.now() - vorher.zeit < WIEDERHOLEN_MS && vorher.antwort) {
    antwort = vorher.antwort;
  } else if (!msg.seite) {
    // Tempo (2.4.0): portal.js fragt erst ohne Seite — nur ein unbekanntes Inserat wird eingepackt und geschickt
    return { bekannt: false };
  } else {
    // 2.6.0 (Nr. 13): laeuft fuer dieses Inserat schon ein Hochladen (F5, zweiter Tab), darauf warten statt zweimal
    let lauf = laufendeInserate.get(kennung);
    const erster = !lauf;
    if (erster) {
      lauf = api(`/werkzeuge/${WERKZEUG}/inserat`, { methode: "POST", daten: { url: msg.url, seite: msg.seite } });
      laufendeInserate.set(kennung, lauf);
      lauf.then(() => laufendeInserate.delete(kennung));
    }
    const r = await lauf;
    if (r.status !== 200 || !r.daten || !r.daten.vergleich_id) {
      return { fehler: r.status === 422 ? "seite" : "server", status: r.status,
               text: fehlertext(r.status, r.daten, "Das Inserat konnte nicht gelesen werden.") };
    }
    antwort = r.daten;
    if (erster) {
      await sitzungAendern((x) => { x.inserate[kennung] = { zeit: Date.now(), antwort, geoeffnet: false, marktlage: {} }; });
    }
  }
  const schonOffen = !!(vorher && vorher.geoeffnet && Date.now() - vorher.zeit < WIEDERHOLEN_MS);
  // Wunsch Ahmad 04.10.2026: hat das Windows-Programm dieses Auto gerade verglichen, sind die Vergleiche schon
  // offen — hier nichts doppelt oeffnen ("Vergleich oeffnen" geht trotzdem). Die Ampel kommt per Direktabruf.
  const vomProgramm = !!antwort.programm_verglichen;
  let geoeffnet = 0;
  let grund = automatik({ e, msg, ausVergleich, schonOffen, vomProgramm });
  if (!grund) grund = automatischErlaubt(kennung);
  if (!grund) {
    geoeffnet = vergleicheStarten(tab, kennung, antwort);
  } else if (grund === "programm" && e.vergleicheOeffnen && !ausVergleich && !schonOffen
             && !Object.keys((vorher && vorher.marktlage) || {}).length) {
    direktAuswerten(kennung, antwort, tab.id).catch(() => {});
  }
  const [jetzt, basis] = await Promise.all([sitzung(), server()]);
  const neu = jetzt.inserate[kennung] || {};
  return { antwort, geoeffnet, ausVergleich, schonOffen: schonOffen || grund === "schon_offen", vomProgramm,
           portale: { mobile: portalWahl.mobile, autoscout: portalWahl.autoscout },
           automatik: grund, marktlage: neu.marktlage || {}, lageFehler: neu.lageFehler || {}, server: basis,
           // 2.6.0 (Nr. 9): in AutoSchnell gibt es eine neuere Version des Helfers
           neueVersion: statusMerker && istNeuer(statusMerker.aktuelle_version, VERSION) ? statusMerker.aktuelle_version : "" };
}

/** 2.6.0 (Nr. 2): Ein im Hintergrund geoeffnetes Inserat ist jetzt sichtbar — erst jetzt von selbst oeffnen. */
async function vergleicheAutomatisch(msg, tab) {
  const kennung = String(msg.kennung || "");
  const [s, e] = await Promise.all([sitzung(), einstellungen()]);
  const i = s.inserate[kennung];
  if (!i || !i.antwort) return { geoeffnet: 0, automatik: "unbekannt" };
  const vergleichsTab = (id) => !!(id && (s.vergleichsTabs[id] || s.programmTabs[id]));
  const grund = automatik({
    e, msg: {}, ausVergleich: vergleichsTab(tab.openerTabId) || vergleichsTab(tab.id),
    schonOffen: !!i.geoeffnet, vomProgramm: !!i.antwort.programm_verglichen,
  }) || automatischErlaubt(kennung);
  if (grund) return { geoeffnet: 0, automatik: grund };
  return { geoeffnet: vergleicheStarten(tab, kennung, i.antwort), automatik: "" };
}

// ------------------------------------------------------------------ Vergleichsseite
// Tempo (04.10.2026, Wunsch Ahmad "noch schneller"): Die Vergleichsseite wird zusaetzlich DIREKT geholt (nur die
// Seite, ohne Anzeige, ~0,5 s statt mehrere Sekunden bis der Hintergrund-Tab geladen ist) — die Ampel steht so
// meist eine Sekunde nach der Box. Klappt das nicht (Pruefseite des Portals, offline), liest wie bisher der Tab.
const portalSchluessel = (portal) => (portal === "AutoScout24" ? "autoscout" : "mobile");

async function packen(text) {
  const strom = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
  const bytes = new Uint8Array(await new Response(strom).arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** Ergebnis einer Vergleichsseite merken (genau einmal je Portal) und an den Inserat-Tab geben. */
async function marktlageMerken(kennung, schluessel, lage, inseratTab) {
  const neu = await sitzungAendern((x) => {
    const i = x.inserate[kennung];
    if (!i || (i.marktlage || {})[schluessel]) return false;
    i.marktlage = { ...(i.marktlage || {}), [schluessel]: lage };
    for (const e of Object.values(x.vergleichsTabs)) {
      if (e.kennung === kennung && portalSchluessel(e.portal) === schluessel) e.erledigt = true;
    }
    return true;
  });
  if (!neu) return false;
  try {
    await chrome.tabs.sendMessage(inseratTab, { typ: "marktlage", kennung, portal: schluessel, lage });
  } catch (e) { /* Inserat-Tab schon zu */ }
  return true;
}

/** 2.6.0: Eine Vergleichsseite war nicht auswertbar — merken und dem Inserat-Tab sagen (statt endlos "wird
 *  ausgewertet"). vorlaeufig = nur der Direktabruf scheiterte; der Tab kann es noch schaffen. */
async function lageFehlerMelden(kennung, schluessel, text, vorlaeufig, inseratTab) {
  const neu = await sitzungAendern((x) => {
    const i = x.inserate[kennung];
    if (!i || (i.marktlage || {})[schluessel]) return false;             // Ampel ist schon da
    const alt = (i.lageFehler || {})[schluessel];
    if (alt && !alt.vorlaeufig && vorlaeufig) return false;               // endgueltig bleibt endgueltig
    i.lageFehler = { ...(i.lageFehler || {}), [schluessel]: { text, vorlaeufig } };
    return true;
  });
  if (!neu || !inseratTab) return;
  try {
    await chrome.tabs.sendMessage(inseratTab, { typ: "marktlage_fehler", kennung, portal: schluessel, text, vorlaeufig });
  } catch (e) { /* Inserat-Tab schon zu */ }
}

// 2.6.0 (Pruefung 05.10.2026, Nr. 4): direkt geholt wird nur bei AutoScout24. mobile.de schuetzt sich gegen
// Abrufe ohne Browser-Fenster (im Test: 403) — ein zweiter Abruf je Vergleichsseite koennte dort das Cookie des
// Nutzers so verschlechtern, dass er beim normalen Surfen Pruefseiten bekommt. Die Ampel kommt dort aus dem Tab.
const DIREKT_PORTALE = ["AutoScout24"];
const DIREKT_MS = 15000;

async function direktAuswerten(kennung, antwort, inseratTab) {
  await Promise.all(erlaubteLinks(antwort).filter((l) => DIREKT_PORTALE.includes(l.portal)).map(async (link) => {
    const schluessel = portalSchluessel(link.portal);
    const abbruch = new AbortController();
    const uhr = setTimeout(() => abbruch.abort(), DIREKT_MS);
    try {
      const r = await fetch(link.url, { credentials: "include", cache: "no-store", signal: abbruch.signal,
                                        headers: { Accept: "text/html,application/xhtml+xml" } });
      if (!r.ok) {
        await lageFehlerMelden(kennung, schluessel, `${link.portal} hat die Vergleichsseite nicht geliefert (${r.status})`,
                               true, inseratTab);
        return;
      }
      const seite = await packen(await r.text());
      const s = await sitzung();
      if (((s.inserate[kennung] || {}).marktlage || {})[schluessel]) return;     // der Tab war schneller
      const ziel = r.url && new URL(r.url).host === new URL(link.url).host ? r.url : link.url;
      const res = await api(`/werkzeuge/${WERKZEUG}/marktlage`, {
        methode: "POST", daten: { vergleich_id: antwort.vergleich_id, url: ziel, seite },
      });
      if (res.status === 200 && res.daten) await marktlageMerken(kennung, schluessel, res.daten, inseratTab);
      else if (res.status === 409) { /* Adresse nach Weiterleitung passt nicht zur Suche: der Tab liefert */ }
      else await lageFehlerMelden(kennung, schluessel, fehlertext(res.status, res.daten, "Vergleichsseite nicht auswertbar"),
                                  true, inseratTab);
    } catch (e) {
      /* offline, Zeitgrenze o. ae. — dann liest der Tab; die Box sagt es nach der Zeitgrenze */
    } finally {
      clearTimeout(uhr);
    }
  }));
}

async function sucheBereit(msg, tab) {
  const s = await sitzung();
  const e = s.vergleichsTabs[tab.id];
  if (e) {
    const schon = ((s.inserate[e.kennung] || {}).marktlage || {})[portalSchluessel(e.portal)];
    return { senden: !e.erledigt && !schon };
  }
  // Wunsch Ahmad 04.10.2026: eine Vergleichsseite, die das Windows-Programm DESSELBEN Kontos gerade geoeffnet
  // hat (letzte 30 min, dieselbe Suche)? Erst nur die Adresse fragen — die Seite geht nur bei einem Treffer raus.
  if (s.programmTabs[tab.id]) return { senden: false };
  const { schluessel } = await lokal("schluessel");
  if (!schluessel) return { senden: false };
  // 2.6.0 (Nr. 15): ohne verbundenes Windows-Programm gibt es keine Programm-Vergleiche — nicht jedes Mal fragen
  const merker = await statusHolen();
  if (merker && merker.programm_verbunden === false) return { senden: false };
  const r = await api(`/werkzeuge/${WERKZEUG}/programm-suche`, {
    methode: "POST", daten: { url: String(msg.url || tab.url || "") },
  });
  if (r.status !== 200 || !r.daten || !r.daten.vergleich_id) return { senden: false };
  await sitzungAendern((x) => {
    x.programmTabs[tab.id] = { vergleich_id: r.daten.vergleich_id, portal: r.daten.portal, zeit: Date.now() };
  });
  return { senden: true, programm: { fahrzeug: r.daten.fahrzeug || {}, portal: r.daten.portal } };
}

// 2.6.2 (Pruefung 05.10.2026, Paket 1): "erledigt" wird VOR der Anfrage gesetzt (genau einmal senden). Scheiterte
// sie nur voruebergehend (kein Netz, Server kurz weg, Bremse) oder gehoerte die Seite zu einer anderen Suche
// (409: der Tab lud noch die Suche des vorigen Autos), blieb der Tab fuer immer "erledigt" — nie mehr eine Ampel.
const istVorlaeufig = (status) => status === 0 || status === 429 || status >= 500;

async function programmSucheBearbeiten(msg, tab) {
  const p = (await sitzung()).programmTabs[tab.id];
  if (!p || p.erledigt) return { fehler: "kein_vergleich" };
  await sitzungAendern((x) => { if (x.programmTabs[tab.id]) x.programmTabs[tab.id].erledigt = true; });  // genau einmal
  const r = await api(`/werkzeuge/${WERKZEUG}/marktlage`, {
    methode: "POST", daten: { vergleich_id: p.vergleich_id, url: msg.url, seite: msg.seite },
  });
  if (r.status !== 200 || !r.daten) {
    const nochmal = istVorlaeufig(r.status);
    if (nochmal) await sitzungAendern((x) => { if (x.programmTabs[tab.id]) x.programmTabs[tab.id].erledigt = false; });
    return { fehler: nochmal ? "vorlaeufig" : "server",
             text: fehlertext(r.status, r.daten, "Die Vergleichsseite konnte nicht ausgewertet werden.") };
  }
  return { lage: r.daten };
}

async function sucheBearbeiten(msg, tab) {
  const s = await sitzung();
  const eintrag = s.vergleichsTabs[tab.id];
  if (!eintrag && s.programmTabs[tab.id]) return programmSucheBearbeiten(msg, tab);
  if (!eintrag || eintrag.erledigt) return { fehler: "kein_vergleich" };
  await sitzungAendern((x) => { if (x.vergleichsTabs[tab.id]) x.vergleichsTabs[tab.id].erledigt = true; });  // genau einmal
  const r = await api(`/werkzeuge/${WERKZEUG}/marktlage`, {
    methode: "POST", daten: { vergleich_id: eintrag.vergleich_id, url: msg.url, seite: msg.seite },
  });
  if (r.status !== 200 || !r.daten) {
    const text = fehlertext(r.status, r.daten, "Die Vergleichsseite konnte nicht ausgewertet werden.");
    const nochmal = istVorlaeufig(r.status);
    if (nochmal || r.status === 409) {
      // nur zuruecknehmen, wenn der Tab noch zu DIESEM Vergleich gehoert (inzwischen kann das naechste Auto dran sein)
      await sitzungAendern((x) => {
        const e = x.vergleichsTabs[tab.id];
        if (e && e.vergleich_id === eintrag.vergleich_id) e.erledigt = false;
      });
    }
    // 409 = diese Seite gehoert zu einer anderen Suche (der Tab laedt gleich die richtige): nichts melden
    if (r.status === 409) return { fehler: "andere_suche" };
    // 2.6.0: auch der Tab schaffte es nicht — die Box im Inserat sagt es gleich (nicht erst nach der Zeitgrenze)
    await lageFehlerMelden(eintrag.kennung, portalSchluessel(eintrag.portal), text, nochmal, eintrag.inseratTab);
    return { fehler: nochmal ? "vorlaeufig" : "server", text };
  }
  await marktlageMerken(eintrag.kennung, portalSchluessel(eintrag.portal), r.daten, eintrag.inseratTab);
  return { lage: r.daten };
}

// ------------------------------------------------------------------ Kaufvertrag
// Wunsch Ahmad 04.10.2026: "unbedingt nicht AutoSchnell als Webseite oeffnen — nur wenn keine App installiert ist,
// ansonsten immer die App". Reihenfolge:
//   1. App-Fenster offen  -> nach vorne holen, darin zum Vertrag wechseln (ohne Neuladen, content.js -> App)
//   2. sonst per Link-Typ web+autoschnell: starten (manifest.json protocol_handlers) — seit 2.6.1 auch, wenn der
//      Helfer die App noch nie gesehen hat (Befund Ahmad 05.10.: in Chrome frisch installiert -> es ging gleich die
//      Webseite auf). Kommt kein App-Fenster, macht der Helfer NICHT selbst die Webseite auf: die App kann in einem
//      anderen Browser liegen oder der Browser fragt erst "AutoSchnell oeffnen?" — die Box bietet "Webseite oeffnen".
//   3. Webseite nur, wenn der Nutzer das dort gewaehlt hat (merkt sich der Helfer: appGesehen = 0, bis die App wieder
//      als App laeuft) oder es keine Inserats-Adresse gibt
// "&vertrag=1": AutoSchnell oeffnet gleich das Vertragsfenster — mit den Daten, die hier aus der Seite kamen.
const APP_START_MS = 90000;

function neueAppStartKennung() {
  try {
    const u = crypto.randomUUID?.().replace(/-/g, "").toLowerCase() || "";
    if (/^[a-f0-9]{32}$/.test(u)) return u;
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  } catch (e) {
    return null;
  }
}

async function appStandMerken(basis, wert) {
  const { appGesehen } = await lokal("appGesehen");
  await chrome.storage.local.set({ appGesehen: { ...(appGesehen && typeof appGesehen === "object" ? appGesehen : {}),
                                                 [basis]: wert } });
}

async function appFenster(basis) {
  let tabs = [];
  try { tabs = await chrome.tabs.query({ url: basis + "/*" }); } catch (e) { tabs = []; }
  for (const t of tabs) {
    try {
      const fenster = await chrome.windows.get(t.windowId);
      if (fenster && fenster.type === "app") return t;
    } catch (e) { /* Fenster zu */ }
  }
  return null;
}

function anTab(tabId, nachricht) {
  return new Promise((fertig) => {
    try {
      chrome.tabs.sendMessage(tabId, nachricht, (antwort) => fertig(chrome.runtime.lastError ? null : antwort));
    } catch (e) {
      fertig(null);
    }
  });
}

function inseratKennungAusUrl(href) {
  let u;
  try { u = new URL(String(href || "")); } catch (e) { return null; }
  const host = u.hostname.toLowerCase();
  if (host === "suchen.mobile.de") {
    const p = u.pathname;
    const id = p.startsWith("/fahrzeuge/details.html") ? u.searchParams.get("id")
      : p.startsWith("/auto-inserat/") ? (/\/(\d{6,})\.html$/.exec(p) || [])[1] : null;
    return id && /^\d{6,20}$/.test(id) ? "mobile:" + id : null;
  }
  if (/^www\.autoscout24\.(de|at|ch)$/.test(host) && u.pathname.toLowerCase().includes("/angebote/")) {
    const m = /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i.exec(u.pathname);
    return m ? "autoscout24:" + m[1].toLowerCase() : null;
  }
  if (/(^|\.)kleinanzeigen\.de$/.test(host)) {
    const m = /\/s-anzeige\/(?:[^/]+\/)?(\d{6,})-216(?:-|$)/.exec(u.pathname);
    return m ? "kleinanzeigen:" + m[1] : null;
  }
  return null;
}

/** Letzte Sicherung vor dem Vertrags-Handoff: der aktuell sichtbare Portal-Tab
 * muss noch GENAU das Inserat zeigen, dessen Box/Session wir benutzen. */
function tabZeigtInserat(tab, kennung) {
  return inseratKennungAusUrl((tab && (tab.pendingUrl || tab.url)) || "") === String(kennung || "");
}

/** Nicht das alte Message-Tab-Objekt vertrauen: nach einem await kann eine
 * SPA im selben Tab bereits auf das naechste Fahrzeug gewechselt haben. */
async function tabZeigtInseratAktuell(tabId, kennung) {
  if (!Number.isInteger(tabId)) return false;
  try {
    const aktuell = await chrome.tabs.get(tabId);
    return tabZeigtInserat(aktuell, kennung);
  } catch (e) {
    return false; // Tab geschlossen/ersetzt -> niemals alten Vertrag oeffnen
  }
}

async function vertragsZiel(kennung) {
  const s = await sitzung();
  const i = s.inserate[String(kennung || "")];
  if (!i || !i.antwort || !i.antwort.app_pfad) return null;
  // 2.6.3 (Paket 2): nur ein Pfad in der App (nie "//fremd.de/…" oder "@fremd.de") — auch wenn der Server falsch antwortet
  const pfad = String(i.antwort.app_pfad);
  if (!pfad.startsWith("/app/") || pfad.startsWith("//") || /[@\\]/.test(pfad.split("?")[0])) return null;
  const trenner = pfad.includes("?") ? "&" : "?";
  return { pfad: pfad + trenner + "vertrag=1", inseratUrl: i.antwort.inserat_url };
}

async function webseiteOeffnen(basis, pfad, tab) {
  try {
    await chrome.tabs.create({ url: basis + pfad, windowId: tab.windowId, index: tab.index + 1, openerTabId: tab.id });
  } catch (e) {
    await chrome.tabs.create({ url: basis + pfad });            // Fenster ohne Tabs (App/Popup)
  }
  return { ok: true, weg: "webseite" };
}

async function vertragOeffnen(msg, tab) {
  // P1-Sicherung: zwischen Box-Anzeige und Klick kann eine SPA schon auf das
  // naechste Fahrzeug gewechselt haben. Das vom Message-Event gelieferte
  // tab-Objekt kann bereits veraltet sein — deshalb live aus Chrome lesen.
  const veraltet = () => ({
    fehler: "veraltet",
    text: "Das angezeigte Fahrzeug hat sich geändert – AutoSchnell liest das aktuelle Inserat neu. Bitte danach noch einmal auf Kaufvertrag klicken.",
  });
  if (!await tabZeigtInseratAktuell(tab?.id, msg.kennung)) return veraltet();

  const ziel = await vertragsZiel(msg.kennung);
  if (!ziel) return { fehler: "unbekannt" };
  const basis = await server();
  // 1. offenes App-Fenster
  const app = await appFenster(basis);

  // P0/P1 08.10.2026: vertragsZiel/server/appFenster enthalten awaits.
  // In dieser Zeit kann mobile.de/AutoScout/Kleinanzeigen im selben SPA-Tab
  // schon Auto B anzeigen. Unmittelbar VOR dem Handoff noch einmal live
  // pruefen; die Antwort/Session von Auto A wird dann komplett verworfen.
  if (!await tabZeigtInseratAktuell(tab?.id, msg.kennung)) return veraltet();
  if (app) {
    const antwort = await anTab(app.id, { type: "AUTOSCHNELL_OEFFNEN", ziel: ziel.pfad });
    await chrome.windows.update(app.windowId, { focused: true });
    if (antwort?.wartet) {
      // App ist offen, hat das Ziel aber wegen ungespeicherter Arbeit noch
      // nicht uebernommen. Nicht behaupten, der Vertrag sei geoeffnet.
      return { ok: true, weg: "app_wartet" };
    }
    if (antwort?.timeout) {
      // Das Fahrzeug wurde innerhalb des langen Handoff-Fensters nicht
      // bestaetigt. Das ist NICHT automatisch ungespeicherte Arbeit.
      return { ok: true, weg: "app_timeout" };
    }
    if (!antwort || !antwort.ok) {
      // Die App lief schon vor dem (aktualisierten) Helfer — nicht hart neu
      // laden (ein halb ausgefuellter Kaufvertrag waere weg). Nach vorne holen
      // und sagen, was zu tun ist.
      return { ok: true, weg: "app_neu_laden" };
    }
    return { ok: true, weg: "app" };
  }
  // 3. der Nutzer hat in der Box "Webseite oeffnen" gewaehlt -> merken, ab jetzt gleich die Webseite
  if (msg.webseite) {
    await appStandMerken(basis, 0);
    return webseiteOeffnen(basis, ziel.pfad, tab);
  }
  // 2. App starten (das Seiten-Skript klickt den Link, noch im Klick des Nutzers) — ausser der Nutzer hat gesagt,
  //    dass es hier keine App gibt
  const { appGesehen } = await lokal("appGesehen");
  const stand = appGesehen && typeof appGesehen === "object" ? appGesehen[basis] : undefined;
  // Auch der Storage-Zugriff ist asynchron — letzter Identitaetscheck direkt
  // vor Protocol/Web-Handoff.
  if (!await tabZeigtInseratAktuell(tab?.id, msg.kennung)) return veraltet();
  if (stand !== 0 && ziel.inseratUrl) {
    const start = neueAppStartKennung();
    const protokoll = "web+autoschnell:vertrag?url=" + encodeURIComponent(ziel.inseratUrl)
      + (start ? "&start=" + start : "");
    return { protokoll, start };
  }
  return webseiteOeffnen(basis, ziel.pfad, tab);
}

/** Nach dem Start per Link-Typ: App-Fenster da -> merken; sonst "unklar" — die Box fragt, statt selbst die Webseite
 *  aufzumachen (die App koennte in einem anderen Browser aufgegangen sein). */
async function appStartPruefen(msg, tab) {
  const basis = await server();
  const start = String(msg?.start || "");
  const ende = Date.now() + APP_START_MS;

  // Neue Version: nicht irgendein App-Fenster zaehlt, sondern nur die
  // serverseitige Bestaetigung, dass genau dieses Konto genau diesen
  // Start-Token NACH erfolgreichem Fahrzeugladen gemeldet hat.
  if (/^[a-f0-9]{32}$/.test(start)) {
    while (Date.now() < ende) {
      const r = await api(`/werkzeuge/${WERKZEUG}/app-start/${start}`);
      if (r.status === 200 && r.daten?.bestaetigt === true) {
        await appStandMerken(basis, Date.now());
        return { ok: true, weg: "app" };
      }
      // Verbindung/Abo weg: nicht 90 s blind weiterpolling.
      if ([401, 402, 403, 404].includes(r.status)) {
        return { ok: true, weg: "unklar" };
      }
      await new Promise((fertig) => setTimeout(fertig, 750));
    }
    return { ok: true, weg: "unklar" };
  }

  // Rueckwaertskompatibilitaet zu bereits erzeugten Protokoll-Links alter
  // Erweiterungen: nur dort bleibt die schwächere Fensterpruefung.
  while (Date.now() < Math.min(ende, Date.now() + 8000)) {
    if (await appFenster(basis)) {
      await appStandMerken(basis, Date.now());
      return { ok: true, weg: "app" };
    }
    await new Promise((fertig) => setTimeout(fertig, 500));
  }
  return { ok: true, weg: "unklar" };
}

/** Je AutoSchnell-Adresse merken, wann sie zuletzt als installierte App lief (Reihenfolge der Einrichtung egal). */
async function appGesehenMerken(sender) {
  let herkunft = "";
  try { herkunft = new URL(sender.url).origin; } catch (e) { return { ok: false }; }
  if (!serverErlaubt(herkunft)) return { ok: false };
  await appStandMerken(herkunft, Date.now());
  return { ok: true };
}

async function vergleicheManuell(msg, tab) {
  const [s] = await Promise.all([sitzung(), einstellungen()]);
  const kennung = String(msg.kennung || "");
  const i = s.inserate[kennung];
  if (!i || !i.antwort) return { fehler: "unbekannt" };
  const geoeffnet = vergleicheStarten(tab, kennung, i.antwort);
  // Links da, aber kein Portal gewaehlt (Fenster am Symbol): sagen, statt stumm nichts zu oeffnen
  const keinPortal = geoeffnet === 0 && (i.antwort.links || []).some((l) => l && erlaubterLink(l.url));
  return { geoeffnet, kein_portal: keinPortal };
}

// ------------------------------------------------------------------ Fenster (popup.html)
async function verbinden(msg) {
  const code = String(msg.code || "").replace(/\D/g, "");
  if (code.length !== 6) return { fehler: "Bitte den 6-stelligen Code aus AutoSchnell eintippen." };
  if (msg.server !== undefined) {
    const s = String(msg.server || "").trim().replace(/\/+$/, "") || SERVER_STANDARD;
    if (!serverErlaubt(s)) return { fehler: "Diese Server-Adresse ist nicht erlaubt." };
    await chrome.storage.local.set({ server: s });
  }
  const r = await api(`/werkzeuge/${WERKZEUG}/verbinden`, {
    methode: "POST", ohneSchluessel: true,
    daten: { code, pc_name: browserName(), pc_kennung: await geraetKennung() },
  });
  if (r.status !== 200 || !r.daten || !r.daten.schluessel) {
    return { fehler: fehlertext(r.status, r.daten, "Verbinden hat nicht geklappt.") };
  }
  await chrome.storage.local.set({
    schluessel: r.daten.schluessel, konto: r.daten.konto || "", name: r.daten.name || "", firma: r.daten.firma || "",
  });
  await chrome.storage.local.remove(["getrenntGrund", "statusMerker"]);
  await sitzungLeeren();
  return { ok: true, konto: r.daten.konto, name: r.daten.name, firma: r.daten.firma };
}

async function status(msg) {
  const daten = await lokal(["schluessel", "konto", "name", "firma", "getrenntGrund", "server"]);
  const e = await einstellungen();
  if (!daten.schluessel) {
    return { verbunden: false, grund: daten.getrenntGrund || "", einstellungen: e, server: await server(), version: VERSION };
  }
  if (msg && msg.schnell) {
    // 2.6.3 (Paket 3): das Fenster zeigt sofort, was es weiss (Konto, Name, Firma) — der Server-Stand folgt
    return { verbunden: true, konto: daten.konto, name: daten.name, firma: daten.firma, vorlaeufig: true,
             einstellungen: e, server: await server(), version: VERSION };
  }
  const r = await api(`/werkzeuge/${WERKZEUG}/status`);
  if (r.status === 200 && r.daten) {
    return { verbunden: true, ...r.daten, einstellungen: e, server: await server(), version: VERSION };
  }
  if (r.status === 401) {
    return { verbunden: false, grund: fehlertext(401, r.daten, "Nicht mehr verbunden."), einstellungen: e, server: await server(), version: VERSION };
  }
  // offline / Abo / Sperre: Verbindung bleibt, Hinweis zeigen
  return { verbunden: true, konto: daten.konto, name: daten.name, firma: daten.firma, hinweis: fehlertext(r.status, r.daten, "Status gerade nicht abrufbar."),
           einstellungen: e, server: await server(), version: VERSION };
}

async function trennen() {
  const { schluessel } = await lokal("schluessel");
  // 2.6.3 (Paket 3): erst lokal trennen (sofort), dann dem Server sagen — vorher wartete der Knopf bis 25 s
  await chrome.storage.local.remove(["schluessel", "konto", "name", "firma", "getrenntGrund", "statusMerker"]);
  await sitzungLeeren();
  if (schluessel) {
    const basis = await server();
    fetch(basis + "/api/werkzeuge/" + WERKZEUG + "/abmelden", {
      method: "POST", credentials: "omit", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Werkzeug-Version": VERSION, "X-Werkzeug-Schluessel": schluessel },
    }).catch(() => {});
  }
  return { ok: true };
}

async function einstellungenSetzen(msg) {
  const e = { ...(await einstellungen()), ...(msg.einstellungen || {}) };
  const neu = { vergleicheOeffnen: !!e.vergleicheOeffnen, mobile: e.mobile !== false, autoscout: e.autoscout !== false };
  await chrome.storage.local.set({ einstellungen: neu });
  portalWahl = { mobile: neu.mobile, autoscout: neu.autoscout };
  return { ok: true, einstellungen: neu };
}

// ------------------------------------------------------------------ Nachrichten
const AKTIONEN = {
  frueh: () => fruehBearbeiten(),
  inserat: (m, t) => inseratBearbeiten(m, t),
  suche_bereit: (m, t) => sucheBereit(m, t),
  suche: (m, t) => sucheBearbeiten(m, t),
  vertrag: (m, t) => vertragOeffnen(m, t),
  app_start_pruefen: (m, t) => appStartPruefen(m, t),
  vergleiche_oeffnen: (m, t) => vergleicheManuell(m, t),
  vergleiche_auto: (m, t) => vergleicheAutomatisch(m, t),
};
const PORTAL = /^https:\/\/(suchen\.mobile\.de|www\.autoscout24\.(de|at|ch)|(www\.)?kleinanzeigen\.de)\//;
const FENSTER = { verbinden, status: (m) => status(m), trennen, einstellungen: einstellungenSetzen };

/** 2.6.3: Der Box-Zustand (zugeklappt) liegt im Hintergrund — Seiten-Skripte lesen storage.local nicht mehr selbst
 *  (der Schluessel liegt dort; setAccessLevel TRUSTED_CONTEXTS sperrt den Speicher fuer Seiten-Skripte). */
async function boxZustand(msg) {
  if (msg.setzen !== undefined) await chrome.storage.local.set({ boxZugeklappt: !!msg.setzen });
  const { boxZugeklappt } = await lokal("boxZugeklappt");
  return { zugeklappt: !!boxZugeklappt };
}
AKTIONEN.box = (m) => boxZustand(m);
try {
  Promise.resolve(chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" })).catch(() => {});
} catch (e) { /* aeltere Browser kennen es nicht — die Seiten-Skripte greifen ohnehin nicht mehr zu */ }
const INTERN_TEXT = "In der AutoSchnell-Erweiterung ist etwas schiefgelaufen – bitte die Seite neu laden.";

/** Oberster Rahmen eines Tabs. 2.6.0 (Nr. 10): vorgeladene Seiten (Speculation Rules, Adresszeile) haben dort eine
 *  andere frameId als 0 — massgeblich ist der Rahmentyp. */
const obersterRahmen = (sender) => sender.frameId === 0 || sender.frameType === "outermost_frame";

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || sender.id !== chrome.runtime.id) return false;
  if (msg.type === "AUTOSCHNELL_FETCH") {
    if (!appHerkunft(sender)) {
      sendResponse({ ok: false, error: "Nur aus AutoSchnell erlaubt." });
      return false;
    }
    abrufHelfer(msg, sendResponse);
    return true;
  }
  // content.js meldet: AutoSchnell laeuft hier als installierte App
  if (msg.type === "AUTOSCHNELL_APP" && sender.tab) {
    appGesehenMerken(sender).then(sendResponse, () => sendResponse({ ok: false }));
    return true;
  }
  // Seiten-Skripte: nur aus dem obersten Rahmen eines Portal-Tabs
  // 2.6.3 (Paket 2): nur von den Portalseiten — nicht von der AutoSchnell-Seite oder sonst irgendwo
  if (AKTIONEN[msg.typ] && sender.tab && obersterRahmen(sender) && PORTAL.test(String(sender.url || ""))) {
    AKTIONEN[msg.typ](msg, sender.tab).then(sendResponse, (e) => {
      console.error("AutoSchnell Helfer:", msg.typ, e);       // Nr. 17: Englisches nie in die Box
      sendResponse({ fehler: "intern", text: INTERN_TEXT });
    });
    return true;
  }
  // Fenster der Erweiterung (popup.html — auch, wenn es als Tab geoeffnet ist); nie aus einer Webseite
  if (FENSTER[msg.typ] && String(sender.url || "").startsWith(chrome.runtime.getURL(""))) {
    FENSTER[msg.typ](msg).then(sendResponse, (e) => sendResponse({ fehler: String(e && e.message || e) }));
    return true;
  }
  return false;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  // 2.6.0 (Nr. 17): nur schreiben, wenn der Tab bekannt ist — vorher schrieb JEDES Schliessen den ganzen Zustand neu
  sitzung().then((s) => {
    if (!s.vergleichsTabs[tabId] && !s.programmTabs[tabId]) return null;
    return sitzungAendern((x) => { delete x.vergleichsTabs[tabId]; delete x.programmTabs[tabId]; });
  }).catch(() => {});
});
