/*
 * Hilfsfunktionen der Playwright-E2E-Suite.
 *
 * - API-Client auf fetch-Basis (Node >= 18) gegen E2E_API_URL
 * - Testdaten mit Zufalls-Suffix (E-Mails @e2etest-mail.de), Aufraeumen per API
 * - Anmeldung im Browser ueber localStorage-Token statt Formular
 *
 * Zugangsdaten der geseedeten Konten kommen aus der Umgebung (CI) oder — nur
 * lokal — aus backend/.env. Sie tauchen weder im Code noch in Ausgaben auf.
 *
 * WICHTIG (Single-Session): jede Anmeldung eines Kontos macht dessen fruehere
 * Tokens ungueltig. Deshalb laeuft die Suite mit EINEM Worker, der Super-Admin-
 * Token wird gecacht und bei 401 einmal neu geholt. Ein Token, das eine
 * Browser-Seite benutzt, darf waehrenddessen nicht durch ein neues Login
 * desselben Kontos ersetzt werden.
 */
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const API_URL = (process.env.E2E_API_URL || "http://localhost:8002/api").replace(/\/+$/, "");
const PASSWORD = "E2eTest123!x";           // >= 10 Zeichen, Ziffer + Sonderzeichen
const MAIL_DOMAIN = "e2etest-mail.de";

function backendEnv() {
  const file = path.resolve(__dirname, "..", "..", "backend", ".env");
  const out = {};
  if (!fs.existsSync(file)) return out;
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (m) out[m[1]] = m[2].trim().replace(/^(['"])(.*)\1$/, "$2");
  }
  return out;
}
const ENV = backendEnv();
const cred = (k) => process.env[`E2E_${k}`] || process.env[k] || ENV[k] || "";
const SUPER_ADMIN = { username: cred("SUPER_ADMIN_USERNAME"), password: cred("SUPER_ADMIN_PASSWORD") };

// ---------- API-Client ----------
class ApiError extends Error {
  constructor(method, p, status, body) {
    super(`${method} ${p} -> ${status}: ${typeof body === "string" ? body : JSON.stringify(body)}`);
    this.status = status;
    this.body = body;
  }
}

async function api(method, p, { token, body, ok = true } = {}) {
  const headers = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${API_URL}${p}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data = text;
  try { data = text ? JSON.parse(text) : null; } catch { /* Klartext */ }
  if (ok && !res.ok) throw new ApiError(method, p, res.status, data);
  return { status: res.status, data };
}
const get = (p, o) => api("GET", p, o).then((r) => r.data);
const post = (p, body, o) => api("POST", p, { ...o, body: body ?? {} }).then((r) => r.data);
const put = (p, body, o) => api("PUT", p, { ...o, body }).then((r) => r.data);
const del = (p, o) => api("DELETE", p, o).then((r) => r.data);

async function login(email, password) {
  const d = await post("/auth/login", { email, password });
  return d.token;
}

let superToken = null;
let superLogin = null;     // laufende Anmeldung — gleichzeitige Aufrufer teilen sie sich
async function superAdmin({ fresh = false } = {}) {
  if (superToken && !fresh) return superToken;
  if (!superLogin) {
    superLogin = (async () => {
      if (!SUPER_ADMIN.username || !SUPER_ADMIN.password) {
        throw new Error("SUPER_ADMIN_USERNAME / SUPER_ADMIN_PASSWORD fehlen (Umgebung oder backend/.env)");
      }
      superToken = await login(SUPER_ADMIN.username, SUPER_ADMIN.password);
      return superToken;
    })().finally(() => { superLogin = null; });
  }
  return superLogin;
}

// Super-Admin-Aufruf; bei 401 (Sitzung anderweitig ersetzt, z.B. durch das
// Formular-Login im Browser) einmal mit frischem Token wiederholen.
async function withSuper(fn) {
  const used = await superAdmin();
  try {
    return await fn(used);
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 401) throw e;
    const token = superToken !== used ? superToken : await superAdmin({ fresh: true });
    return fn(token);
  }
}
const superGet = (p) => withSuper((t) => get(p, { token: t }));
const superPost = (p, body) => withSuper((t) => post(p, body, { token: t }));
const superPut = (p, body) => withSuper((t) => put(p, body, { token: t }));
const superDel = (p) => withSuper((t) => del(p, { token: t }));

// ---------- Testdaten ----------
const suffix = () => crypto.randomBytes(4).toString("hex");
const mail = (prefix, s) => `${prefix}-${s}@${MAIL_DOMAIN}`;

/** Kalendertag JJJJ-MM-TT in lokaler Zeit (wie die Oberflaeche rechnet). */
function isoDate(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Firma (Haendler-Hauptaccount) ohne Abo, angelegt vom Super-Admin. */
async function createFirma({ s = suffix(), companyName } = {}) {
  const email = mail("e2e-chef", s);
  const company_name = companyName || `E2E Autohaus ${s}`;
  const r = await superPost("/admin/users", { email, password: PASSWORD, company_name, plan_type: "none" });
  const token = await login(email, PASSWORD);
  return { s, email, password: PASSWORD, userId: r.user_id, dealerId: r.dealer_id, companyName: company_name, token };
}

/** Sucher der Firma; abo=true schaltet die Sucher-Funktion (Monat) frei. */
async function createSucher(firma, { s = suffix(), abo = false } = {}) {
  const email = mail("e2e-sucher", s);
  const firstName = "Erika";
  const lastName = `Sucher${s}`;
  const r = await superPost(`/admin/dealers/${firma.dealerId}/sucher`,
    { email, password: PASSWORD, first_name: firstName, last_name: lastName });
  if (abo) await superPost(`/admin/sucher/${r.sucher_id}/abo`, { plan: "monthly" });
  const token = await login(email, PASSWORD);
  return { s, email, password: PASSWORD, userId: r.sucher_id, dealerId: firma.dealerId, name: `${firstName} ${lastName}`, token };
}

/** Normaler Admin (kein Betreiber): Firma anlegen, Rolle auf admin setzen. */
async function createNormalAdmin({ s = suffix() } = {}) {
  const firma = await createFirma({ s, companyName: `E2E Admin ${s}` });
  await superPut(`/admin/users/${firma.userId}`, { role: "admin" });
  const token = await login(firma.email, PASSWORD);
  return { ...firma, token, role: "admin" };
}

async function createDriver({ s = suffix() } = {}) {
  const email = mail("e2e-fahrer", s);
  const displayName = `Fahrer ${s}`;
  const r = await post("/driver/register", { email, password: PASSWORD, display_name: displayName });
  return { s, email, password: PASSWORD, id: r.driver.id, driverCode: r.driver.driver_code, displayName, token: r.token };
}

/** Zwischenhaendler (b2b_buyer); access=true schaltet den Marktplatz frei. */
async function createBuyer({ s = suffix(), access = true } = {}) {
  const email = mail("e2e-kaeufer", s);
  const companyName = `E2E Zwischenhandel ${s}`;
  const r = await post("/buyer/register", {
    company_name: companyName, contact_name: "Kai Kaeufer", email, password: PASSWORD,
    phone: "0511 123456", gewerblich_bestaetigt: true,
  });
  if (access) await superPost(`/admin/buyers/${r.user.id}/access`, { plan: "monthly" });
  return { s, email, password: PASSWORD, id: r.user.id, companyName, token: r.token };
}

async function createAppointment(firma, body) {
  return post("/appointments", { title: "Fahrzeug abholen", status: "offen", pickup_time: "10:00", ...body },
    { token: firma.token });
}

/** Manuelles Fahrzeug -> Inserat -> verkaufsbereit -> veroeffentlicht (public). */
async function publishListing(firma, { pricePublic = 19900, priceB2b = 18500 } = {}) {
  await superPut(`/admin/dealers/${firma.dealerId}/sale-plan`, { tier: "s5" });
  await put("/dealer/marketplace-profile", { public: true, description: "E2E-Testhaendler" }, { token: firma.token });
  const v = await post("/vehicles/manual", {
    make_label: "BMW", model_label: "320d", model_description: `E2E Testwagen ${firma.s}`,
    first_registration: "03/2019", mileage: 85000, fuel_label: "Diesel", gearbox_label: "Automatik",
    power_kw: 140, power_ps: 190, color: "Schwarz", purchase_price: 15000,
  }, { token: firma.token });
  const draft = await post(`/resale/draft/${v.id}`, {}, { token: firma.token });
  await put(`/resale/${draft.id}`, { price_public: pricePublic, price_b2b: priceB2b }, { token: firma.token });
  await post(`/resale/${draft.id}/status`, { status: "verkaufsbereit" }, { token: firma.token });
  await post(`/resale/${draft.id}/publish`, { visibility: "public" }, { token: firma.token });
  return { vehicleId: v.id, listingId: draft.id, title: draft.title };
}

// ---------- Aufraeumen (bestmoeglich, Fehler nur als Warnung) ----------
// Nacheinander, nicht parallel: ein 401 wuerde sonst mehrere gleichzeitige
// Neu-Anmeldungen ausloesen, die sich gegenseitig die Sitzung wegnehmen.
async function versuchen(label, fn) {
  try { await fn(); } catch (e) { console.warn(`[e2e cleanup] ${label}: ${e?.message || e}`); }
}

async function cleanup({ firmen = [], admins = [], drivers = [], buyers = [] } = {}) {
  for (const a of admins.filter(Boolean)) {
    await versuchen(`Admin ${a.email}`, async () => {
      await superPut(`/admin/users/${a.userId}`, { role: "dealer" });
      await superDel(`/admin/users/${a.userId}?firma_loeschen=true`);
    });
  }
  for (const f of firmen.filter(Boolean)) {
    await versuchen(`Firma ${f.email}`, () => superDel(`/admin/users/${f.userId}?firma_loeschen=true`));
  }
  for (const d of drivers.filter(Boolean)) {
    await versuchen(`Fahrer ${d.email}`, () => superDel(`/admin/drivers/${d.id}`));
  }
  for (const b of buyers.filter(Boolean)) {
    await versuchen(`Kaeufer ${b.email}`, () => superDel(`/admin/users/${b.id}`));
  }
}

/**
 * Sicherheitsnetz vor und nach dem Lauf: Reste frueherer (abgebrochener)
 * Laeufe entfernen — ausschliesslich Konten, die DIESE Suite anlegt
 * (e2e-chef-/e2e-sucher-/e2e-fahrer-/e2e-kaeufer-<hex>@e2etest-mail.de);
 * Testdaten der Backend-Pytest-Suiten bleiben unangetastet.
 */
const E2E_MAIL = new RegExp(`^e2e-(chef|sucher|fahrer|kaeufer)-[0-9a-f]{8}@${MAIL_DOMAIN.replace(/\./g, "\\.")}$`, "i");
async function sweepLeftovers() {
  const users = (await superGet("/admin/users")).filter((u) => E2E_MAIL.test(u.email || "") && !u.is_super_admin);
  const drivers = (await superGet("/admin/drivers")).filter((d) => E2E_MAIL.test(d.email || ""));
  const entfernt = [];
  const firmen = users.filter((u) => u.role === "dealer" || u.role === "admin");
  for (const u of firmen) {
    await versuchen(`Rest-Firma ${u.email}`, async () => {
      if (u.role === "admin") await superPut(`/admin/users/${u.id}`, { role: "dealer" });
      await superDel(`/admin/users/${u.id}?firma_loeschen=true`);
      entfernt.push(u.email);
    });
  }
  for (const u of users.filter((u) => !firmen.includes(u))) {          // Sucher, Kaeufer
    await versuchen(`Rest-Konto ${u.email}`, async () => {
      try { await superDel(`/admin/users/${u.id}`); entfernt.push(u.email); }
      catch (e) { if (!(e instanceof ApiError && e.status === 404)) throw e; }   // schon mit der Firma weg
    });
  }
  for (const d of drivers) {
    await versuchen(`Rest-Fahrer ${d.email}`, async () => { await superDel(`/admin/drivers/${d.id}`); entfernt.push(d.email); });
  }
  if (entfernt.length) console.log(`[e2e] Reste frueherer Laeufe entfernt: ${entfernt.join(", ")}`);
  return entfernt.length;
}

// ---------- Browser ----------
const TOKEN_KEY = { app: "ah_token", buyer: "ah_buyer_token", driver: "ah_driver_token" };

/** Token vor jeder Navigation in localStorage legen (kein Formular-Login). */
async function authPage(page, key, token) {
  await page.addInitScript(([k, v]) => { window.localStorage.setItem(k, v); }, [TOKEN_KEY[key] || key, token]);
}

/** Zweite Rolle im selben Test: eigener Browser-Kontext mit Token. */
async function newAuthedPage(browser, key, token, options = {}) {
  const context = await browser.newContext(options);
  await context.addInitScript(([k, v]) => { window.localStorage.setItem(k, v); }, [TOKEN_KEY[key] || key, token]);
  const page = await context.newPage();
  return { context, page };
}

module.exports = {
  API_URL, PASSWORD, SUPER_ADMIN, ApiError,
  api, get, post, put, del, login,
  superAdmin, superGet, superPost, superPut, superDel,
  suffix, isoDate,
  createFirma, createSucher, createNormalAdmin, createDriver, createBuyer,
  createAppointment, publishListing, cleanup, sweepLeftovers,
  authPage, newAuthedPage,
};
