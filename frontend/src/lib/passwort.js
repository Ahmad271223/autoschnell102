// Passwoerter, die der Betreiber vergibt (14.09.2026, Wunsch Ahmad: "Passwort
// muss von mir erstellbar sein, darf 20 Zeichen lang sein, muss immer klappen").
//
// Die Regeln stehen in backend/passwoerter.py (EINE Funktion fuer alle
// Rollen): mindestens 10 Zeichen, hoechstens 72 Bytes, mindestens eine Ziffer
// oder ein Sonderzeichen, nicht nur ein wiederholtes Zeichen, kein
// Allerweltswort. Vorher prueften die Admin-Formulare "mind. 8" — das Backend
// lehnte 8 und 9 Zeichen dann mit 422 ab, was wie "irgendwas klappt nicht"
// aussah. Hier dieselbe Vorpruefung, damit der Betreiber die Ablehnung sofort
// und verstaendlich sieht.
export const PASSWORT_MIN = 10;
export const PASSWORT_MAX_BYTES = 72;

// Pruefbericht 20.09.2026 (K-04): dieselbe Sperrliste und dieselben
// Sonderzeichen wie backend/passwoerter.py (_VERBOTEN, _SONDERZEICHEN). Vorher
// fehlte beides — "Passwort123!" oder "Hannover2026" gingen hier durch und
// scheiterten erst am Server. tests/test_passwort_listen_20260921.py gleicht
// beide Seiten ab.
export const VERBOTEN = new Set([
  "password", "passwort", "passwort1", "password1", "qwertz", "qwerty",
  "123456", "1234567", "12345678", "123456789", "1234567890", "abc123",
  "111111", "letmein", "welcome", "willkommen", "admin", "administrator",
  "iloveyou", "monkey", "dragon", "sunshine", "princess", "football",
  "baseball", "master", "hallo", "hallo123", "schalke", "bayern", "ficken",
  "geheim", "sommer", "winter", "herbst", "fruehling", "test", "testtest",
  "autohaus", "autoschnell", "autohandel", "kfz", "mercedes", "bmw", "audi",
  "porsche", "hannover", "berlin", "muenchen", "hamburg", "deutschland",
  "changeme", "secret", "default", "login", "user", "root", "guest",
]);
export const SONDERZEICHEN = new Set("!@#$%^&*()_+-=[]{}|;':\",./<>?`~\\ ");

const istRand = (c) => /[0-9]/.test(c) || SONDERZEICHEN.has(c);

/** Kern ohne fuehrende/abschliessende Ziffern und Sonderzeichen (wie _stamm). */
export function stamm(pw) {
  let s = String(pw ?? "").trim().toLowerCase();
  while (s && istRand(s[s.length - 1])) s = s.slice(0, -1);
  while (s && istRand(s[0])) s = s.slice(1);
  return s;
}

// Ohne I, l, O, o (Verwechslung beim Abtippen/Vorlesen), mit Ziffern ohne 0/1.
const BUCHSTABEN = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz";
const ZIFFERN = "23456789";
const ALLE = BUCHSTABEN + ZIFFERN;

function zufall(n) {
  const a = new Uint32Array(n);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(a);
  } else {
    for (let i = 0; i < n; i++) a[i] = Math.floor(Math.random() * 4294967296);
  }
  return a;
}

/** Zufaelliges Passwort (Standard 20 Zeichen) mit garantiert zwei Ziffern. */
export function passwortVorschlag(laenge = 20) {
  const n = Math.max(PASSWORT_MIN, Math.min(64, laenge));
  const z = zufall(n);
  const zeichen = [];
  for (let i = 0; i < n; i++) zeichen.push(ALLE[z[i] % ALLE.length]);
  const p = zufall(4);
  const i1 = p[0] % n;
  let i2 = p[1] % n;
  if (i2 === i1) i2 = (i2 + 1) % n;
  zeichen[i1] = ZIFFERN[p[2] % ZIFFERN.length];
  zeichen[i2] = ZIFFERN[p[3] % ZIFFERN.length];
  return zeichen.join("");
}

/** Vorpruefung wie backend/passwoerter.py: "" = in Ordnung, sonst der Grund. */
export function passwortProblem(pw) {
  const s = String(pw ?? "");
  if (s !== s.trim()) return "Passwort darf nicht mit einem Leerzeichen beginnen oder enden";
  if (s.length < PASSWORT_MIN) return `Passwort muss mindestens ${PASSWORT_MIN} Zeichen lang sein`;
  if (new TextEncoder().encode(s).length > PASSWORT_MAX_BYTES) {
    return `Passwort darf höchstens ${PASSWORT_MAX_BYTES} Zeichen lang sein`;
  }
  // K-04: Umlaute zaehlen (wie am Server) NICHT als Sonderzeichen.
  if (![...s].some(istRand)) {
    return "Passwort braucht mindestens eine Ziffer oder ein Sonderzeichen";
  }
  // Nachpruefung 15.09.2026: nur Ziffern (5837294615) reichen nicht.
  if (!/\p{L}/u.test(s)) return "Passwort braucht mindestens einen Buchstaben";
  if (new Set(s).size < 3) return "Passwort ist zu einfach (immer dasselbe Zeichen)";
  if (VERBOTEN.has(stamm(s)) || VERBOTEN.has(s.trim().toLowerCase())) {
    return "Dieses Passwort ist zu bekannt/unsicher — bitte ein anderes wählen";
  }
  return "";
}

/**
 * Text nach "Passwort setzen" (14.09.2026, Ahmad: "Meldung 'Sperre
 * aufgehoben', obwohl nie eine Sperre war"): das Backend sagt jetzt, ob
 * wirklich eine Anmeldesperre bestand.
 */
export function sperreHinweis(daten) {
  const d = daten || {};
  if (d.sperre_aufgehoben) return " Die Anmeldesperre wurde aufgehoben.";
  if (d.unklar) {
    return ` ${d.hinweis || "Stand der Anmeldesperre konnte nicht gelesen werden."}`;
  }
  if (Number(d.fehlversuche) > 0) {
    return ` ${d.fehlversuche} Fehlversuch(e) gelöscht — eine Sperre bestand nicht.`;
  }
  return " Es bestand keine Anmeldesperre.";
}
