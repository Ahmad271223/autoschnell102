// AutoSchnell Helfer — Hintergrund (Service Worker).
//
// 1. Abruf-Helfer (seit 08/2026): holt fuer die AutoSchnell-App Kleinanzeigen-Seiten ueber die Leitung
//    des Nutzers (AUTOSCHNELL_FETCH, content.js) — unveraendert.
// 2. Browser-Helfer (04.10.2026): Inserat geoeffnet -> Seite an AutoSchnell -> Vergleiche mit den
//    Firmenregeln oeffnen sich im Hintergrund, die Vergleichsseite kommt zurueck (Platz + Ampel), die
//    Box im Inserat zeigt alles, "Kaufvertrag" oeffnet das Auto sofort in AutoSchnell.
//    Verbunden wird mit dem 6-stelligen Code aus AutoSchnell (Programme) — der Schluessel kann nur
//    diese Erweiterung bedienen, keine Anmeldung, kein Passwort. Ein Konto = ein Browser.

const VERSION = chrome.runtime.getManifest().version;
const SERVER_STANDARD = "https://app.auto-schnellkauf.de";
const WERKZEUG = "browser-helfer";
const WIEDERHOLEN_MS = 30 * 60 * 1000;     // dasselbe Inserat innerhalb 30 min: nichts neu oeffnen
const ANFRAGE_MS = 25000;

// ------------------------------------------------------------------ 1. Abruf-Helfer (unveraendert)
const KLEINANZEIGEN = /^https:\/\/(www\.)?kleinanzeigen\.de\/s-anzeige\//i;

function abrufHelfer(msg, sendResponse) {
  const url = String(msg.url || "");
  if (!KLEINANZEIGEN.test(url)) {
    sendResponse({ ok: false, error: "Nur Kleinanzeigen-Fahrzeuglinks erlaubt." });
    return;
  }
  fetch(url, { credentials: "omit", headers: { Accept: "text/html,application/xhtml+xml" } })
    .then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text();
    })
    .then((html) => sendResponse({ ok: true, html }))
    .catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
}

// ------------------------------------------------------------------ Verbindung + Server
/** Erlaubte Server: die Live-Adresse; zum Testen localhost/127.0.0.1 (Recht wird dann erfragt). */
function serverErlaubt(s) {
  return s === SERVER_STANDARD || /^http:\/\/(localhost|127\.0\.0\.1)(:\d{2,5})?$/.test(s);
}

async function lokal(schluessel) {
  return chrome.storage.local.get(schluessel);
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
  const basis = await server();
  const { schluessel } = await lokal("schluessel");
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
    if (r.status === 401 && !ohneSchluessel) {
      // Verbindung gilt nicht mehr (anderer Browser, Chef, App) — der Server sagt genau, warum
      await chrome.storage.local.remove(["schluessel"]);
      await chrome.storage.local.set({ getrenntGrund: (antwort && antwort.detail) || "Nicht mehr verbunden." });
    }
    return { status: r.status, daten: antwort };
  } catch (e) {
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
  const s = await chrome.storage.session.get(["inserate", "vergleichsTabs"]);
  return { inserate: s.inserate || {}, vergleichsTabs: s.vergleichsTabs || {} };
}

let sperre = Promise.resolve();
/** Lesen-Aendern-Schreiben streng nacheinander (gleichzeitige Tabs ueberschreiben sich sonst). */
function sitzungAendern(fn) {
  const lauf = sperre.then(async () => {
    const s = await sitzung();
    const ergebnis = await fn(s);
    const jetzt = Date.now();
    for (const [k, v] of Object.entries(s.inserate)) if (jetzt - v.zeit > 2 * 60 * 60 * 1000) delete s.inserate[k];
    await chrome.storage.session.set(s);
    return ergebnis;
  });
  sperre = lauf.catch(() => null);
  return lauf;
}

async function einstellungen() {
  const { einstellungen: e } = await lokal("einstellungen");
  return { vergleicheOeffnen: true, ...(e || {}) };
}

// ------------------------------------------------------------------ Inserat
async function vergleicheOeffnen(tab, kennung, antwort) {
  const links = (antwort && antwort.links) || [];
  const neue = [];
  for (let i = 0; i < links.length; i++) {
    try {
      const t = await chrome.tabs.create({ url: links[i].url, active: false, openerTabId: tab.id, index: tab.index + 1 + i });
      neue.push([t.id, { vergleich_id: antwort.vergleich_id, inseratTab: tab.id, kennung, portal: links[i].portal }]);
    } catch (e) { /* Fenster zu */ }
  }
  await sitzungAendern((s) => {
    for (const [id, eintrag] of neue) s.vergleichsTabs[id] = eintrag;
    if (s.inserate[kennung]) s.inserate[kennung].geoeffnet = true;
  });
  return neue.length;
}

/** Tempo: Vergleichsseiten sofort direkt holen und die Tabs anlegen — ohne dass die Box darauf wartet. */
function vergleicheStarten(tab, kennung, antwort) {
  // sofort vermerken (Neuladen des Inserats oeffnet nichts doppelt), die Tabs folgen gleich
  sitzungAendern((s) => { if (s.inserate[kennung]) s.inserate[kennung].geoeffnet = true; }).catch(() => {});
  direktAuswerten(kennung, antwort, tab.id).catch(() => {});
  vergleicheOeffnen(tab, kennung, antwort).catch(() => {});
  return (antwort.links || []).length;
}

async function inseratBearbeiten(msg, tab) {
  const { schluessel } = await lokal("schluessel");
  if (!schluessel) {
    const { getrenntGrund } = await lokal("getrenntGrund");
    return { fehler: "nicht_verbunden", text: getrenntGrund || "Nicht verbunden – auf das AutoSchnell-Symbol klicken und den Code aus AutoSchnell eintippen." };
  }
  const kennung = String(msg.kennung || "");
  const s = await sitzung();
  const vorher = s.inserate[kennung];
  // Aus einer unserer Vergleichsseiten geoeffnet (neuer Tab) oder darin weitergeklickt (derselbe Tab)?
  // Dann nichts automatisch oeffnen — sonst oeffnet jedes angeschaute Vergleichsauto neue Vergleiche.
  const ausVergleich = !!((tab.openerTabId && s.vergleichsTabs[tab.openerTabId]) || s.vergleichsTabs[tab.id]);
  let antwort;
  if (vorher && Date.now() - vorher.zeit < WIEDERHOLEN_MS && vorher.antwort) {
    antwort = vorher.antwort;
  } else {
    const r = await api(`/werkzeuge/${WERKZEUG}/inserat`, { methode: "POST", daten: { url: msg.url, seite: msg.seite } });
    if (r.status !== 200 || !r.daten || !r.daten.vergleich_id) {
      return { fehler: r.status === 422 ? "seite" : "server", status: r.status,
               text: fehlertext(r.status, r.daten, "Das Inserat konnte nicht gelesen werden.") };
    }
    antwort = r.daten;
    await sitzungAendern((x) => { x.inserate[kennung] = { zeit: Date.now(), antwort, geoeffnet: false, marktlage: {} }; });
  }
  const e = await einstellungen();
  const schonOffen = !!(vorher && vorher.geoeffnet && Date.now() - vorher.zeit < WIEDERHOLEN_MS);
  let geoeffnet = 0;
  if (e.vergleicheOeffnen && !ausVergleich && !schonOffen) geoeffnet = vergleicheStarten(tab, kennung, antwort);
  const neu = (await sitzung()).inserate[kennung] || {};
  return { antwort, geoeffnet, ausVergleich, schonOffen, marktlage: neu.marktlage || {}, server: await server() };
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

async function direktAuswerten(kennung, antwort, inseratTab) {
  await Promise.all((antwort.links || []).map(async (link) => {
    const schluessel = portalSchluessel(link.portal);
    try {
      const r = await fetch(link.url, { credentials: "include", cache: "no-store" });
      if (!r.ok) return;
      const seite = await packen(await r.text());
      const s = await sitzung();
      if (((s.inserate[kennung] || {}).marktlage || {})[schluessel]) return;     // der Tab war schneller
      const ziel = r.url && new URL(r.url).host === new URL(link.url).host ? r.url : link.url;
      const res = await api(`/werkzeuge/${WERKZEUG}/marktlage`, {
        methode: "POST", daten: { vergleich_id: antwort.vergleich_id, url: ziel, seite },
      });
      if (res.status === 200 && res.daten) await marktlageMerken(kennung, schluessel, res.daten, inseratTab);
    } catch (e) { /* dann liest der Tab */ }
  }));
}

async function sucheBereit(tab) {
  const s = await sitzung();
  const e = s.vergleichsTabs[tab.id];
  const schon = e && ((s.inserate[e.kennung] || {}).marktlage || {})[portalSchluessel(e.portal)];
  return { senden: !!e && !e.erledigt && !schon };
}

async function sucheBearbeiten(msg, tab) {
  const s = await sitzung();
  const eintrag = s.vergleichsTabs[tab.id];
  if (!eintrag || eintrag.erledigt) return { fehler: "kein_vergleich" };
  await sitzungAendern((x) => { if (x.vergleichsTabs[tab.id]) x.vergleichsTabs[tab.id].erledigt = true; });  // genau einmal
  const r = await api(`/werkzeuge/${WERKZEUG}/marktlage`, {
    methode: "POST", daten: { vergleich_id: eintrag.vergleich_id, url: msg.url, seite: msg.seite },
  });
  if (r.status !== 200 || !r.daten) {
    return { fehler: "server", text: fehlertext(r.status, r.daten, "Die Vergleichsseite konnte nicht ausgewertet werden.") };
  }
  await marktlageMerken(eintrag.kennung, portalSchluessel(eintrag.portal), r.daten, eintrag.inseratTab);
  return { lage: r.daten };
}

// ------------------------------------------------------------------ Kaufvertrag
// Wunsch Ahmad 04.10.2026: "unbedingt nicht AutoSchnell als Webseite oeffnen — nur wenn keine App installiert ist,
// ansonsten immer die App". Reihenfolge:
//   1. App-Fenster offen  -> nach vorne holen, darin zum Vertrag wechseln (ohne Neuladen, content.js -> App)
//   2. App installiert, aber zu -> per Link-Typ web+autoschnell: starten (manifest.json protocol_handlers);
//      kommt binnen APP_START_MS kein App-Fenster, gilt die App als entfernt -> Webseite
//   3. keine App -> Webseite in neuem Tab
// "&vertrag=1": AutoSchnell oeffnet gleich das Vertragsfenster — mit den Daten, die hier aus der Seite kamen.
const APP_GESEHEN_TAGE = 60;
const APP_START_MS = 10000;

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

async function vertragsZiel(kennung) {
  const s = await sitzung();
  const i = s.inserate[String(kennung || "")];
  if (!i || !i.antwort || !i.antwort.app_pfad) return null;
  return { pfad: i.antwort.app_pfad + "&vertrag=1", inseratUrl: i.antwort.inserat_url };
}

async function webseiteOeffnen(basis, pfad, tab) {
  await chrome.tabs.create({ url: basis + pfad, index: tab.index + 1, openerTabId: tab.id });
  return { ok: true, weg: "webseite" };
}

async function vertragOeffnen(msg, tab) {
  const ziel = await vertragsZiel(msg.kennung);
  if (!ziel) return { fehler: "unbekannt" };
  const basis = await server();
  // 1. offenes App-Fenster
  const app = await appFenster(basis);
  if (app) {
    const antwort = await anTab(app.id, { type: "AUTOSCHNELL_OEFFNEN", ziel: ziel.pfad });
    if (!antwort || !antwort.ok) {
      // App vor dem Helfer geoeffnet (kein Seiten-Skript darin): dorthin wechseln
      await chrome.tabs.update(app.id, { url: basis + ziel.pfad });
    }
    await chrome.windows.update(app.windowId, { focused: true });
    return { ok: true, weg: "app" };
  }
  // 2. installiert (schon einmal als App gesehen), aber zu: das Seiten-Skript startet sie per Link-Typ
  const { appGesehen } = await lokal("appGesehen");
  const gesehenAm = appGesehen && appGesehen[basis];
  if (gesehenAm && Date.now() - gesehenAm < APP_GESEHEN_TAGE * 86400000 && ziel.inseratUrl && !msg.ohneApp) {
    return { protokoll: "web+autoschnell:vertrag?url=" + encodeURIComponent(ziel.inseratUrl) };
  }
  // 3. keine App
  return webseiteOeffnen(basis, ziel.pfad, tab);
}

/** Nach dem Start per Link-Typ: kommt kein App-Fenster, ist die App wohl entfernt -> Webseite (und merken). */
async function appStartPruefen(msg, tab) {
  const basis = await server();
  const ende = Date.now() + APP_START_MS;
  while (Date.now() < ende) {
    if (await appFenster(basis)) return { ok: true, weg: "app" };
    await new Promise((r) => setTimeout(r, 500));
  }
  const { appGesehen } = await lokal("appGesehen");
  await chrome.storage.local.set({ appGesehen: { ...(appGesehen || {}), [basis]: 0 } });
  const ziel = await vertragsZiel(msg.kennung);
  return ziel ? webseiteOeffnen(basis, ziel.pfad, tab) : { fehler: "unbekannt" };
}

/** Je AutoSchnell-Adresse merken, wann sie zuletzt als installierte App lief (Reihenfolge der Einrichtung egal). */
async function appGesehenMerken(sender) {
  let herkunft = "";
  try { herkunft = new URL(sender.url).origin; } catch (e) { return { ok: false }; }
  if (!serverErlaubt(herkunft)) return { ok: false };
  const { appGesehen } = await lokal("appGesehen");
  await chrome.storage.local.set({ appGesehen: { ...(appGesehen && typeof appGesehen === "object" ? appGesehen : {}),
                                                 [herkunft]: Date.now() } });
  return { ok: true };
}

async function vergleicheManuell(msg, tab) {
  const s = await sitzung();
  const kennung = String(msg.kennung || "");
  const i = s.inserate[kennung];
  if (!i || !i.antwort) return { fehler: "unbekannt" };
  return { geoeffnet: vergleicheStarten(tab, kennung, i.antwort) };
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
  await chrome.storage.local.remove("getrenntGrund");
  return { ok: true, konto: r.daten.konto, name: r.daten.name, firma: r.daten.firma };
}

async function status() {
  const daten = await lokal(["schluessel", "konto", "name", "firma", "getrenntGrund", "server"]);
  const e = await einstellungen();
  if (!daten.schluessel) {
    return { verbunden: false, grund: daten.getrenntGrund || "", einstellungen: e, server: await server(), version: VERSION };
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
  if (schluessel) await api(`/werkzeuge/${WERKZEUG}/abmelden`, { methode: "POST" });
  await chrome.storage.local.remove(["schluessel", "konto", "name", "firma", "getrenntGrund"]);
  return { ok: true };
}

async function einstellungenSetzen(msg) {
  const e = { ...(await einstellungen()), ...(msg.einstellungen || {}) };
  await chrome.storage.local.set({ einstellungen: { vergleicheOeffnen: !!e.vergleicheOeffnen } });
  return { ok: true, einstellungen: e };
}

// ------------------------------------------------------------------ Nachrichten
const AKTIONEN = {
  inserat: (m, t) => inseratBearbeiten(m, t),
  suche_bereit: (m, t) => sucheBereit(t),
  suche: (m, t) => sucheBearbeiten(m, t),
  vertrag: (m, t) => vertragOeffnen(m, t),
  app_start_pruefen: (m, t) => appStartPruefen(m, t),
  vergleiche_oeffnen: (m, t) => vergleicheManuell(m, t),
};
const FENSTER = { verbinden, status, trennen, einstellungen: einstellungenSetzen };

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || sender.id !== chrome.runtime.id) return false;
  if (msg.type === "AUTOSCHNELL_FETCH") {
    abrufHelfer(msg, sendResponse);
    return true;
  }
  // content.js meldet: AutoSchnell laeuft hier als installierte App
  if (msg.type === "AUTOSCHNELL_APP" && sender.tab) {
    appGesehenMerken(sender).then(sendResponse, () => sendResponse({ ok: false }));
    return true;
  }
  // Seiten-Skripte: nur aus dem obersten Rahmen eines Portal-Tabs
  if (AKTIONEN[msg.typ] && sender.tab && sender.frameId === 0 && /^https:\/\//.test(String(sender.url || ""))) {
    AKTIONEN[msg.typ](msg, sender.tab).then(sendResponse, (e) => sendResponse({ fehler: "intern", text: String(e && e.message || e) }));
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
  sitzungAendern((s) => { delete s.vergleichsTabs[tabId]; }).catch(() => {});
});
