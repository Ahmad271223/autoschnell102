/*
 * Runde 27 (12.09.2026, Pruefbefund P0): Der zuletzt angezeigte Vergleich lag
 * unter EINEM festen Schluessel im sessionStorage ("ah_vergleich_state") und
 * wurde nur beim normalen Abmelden geloescht.
 *
 * Endete eine Sitzung anders — neue Anmeldung auf einem anderen Gerät, 401 vom
 * Server —, blieb der Stand liegen: Meldete sich danach im selben Browser ein
 * ANDERER Sucher an, sah er Fahrzeug, Verkäuferdaten und den zuletzt
 * erstellten Vertrag seines Kollegen.
 *
 * Regel ab jetzt: Jeder gespeicherte Oberflächen-Zustand gehört zu genau einem
 * Konto (Schlüssel mit user.id) und wird bei jedem Abmelden ODER 401 komplett
 * entfernt — auch die Stände anderer Konten im selben Browser.
 */

export const VERGLEICH_PRAEFIX = "ah_vergleich_state";

/** Schluessel eines Kontos ("ah_vergleich_state:<user.id>"). */
export function vergleichKey(userId) {
  return `${VERGLEICH_PRAEFIX}:${userId || "unbekannt"}`;
}

function sicher(fn, ersatz = null) {
  try { return fn(); } catch { return ersatz; }
}

/** Zustand DIESES Kontos lesen; alles andere wird ignoriert. */
export function vergleichLaden(storage, userId) {
  if (!storage || !userId) return null;
  const roh = sicher(() => storage.getItem(vergleichKey(userId)));
  if (!roh) return null;
  return sicher(() => JSON.parse(roh));
}

export function vergleichSichern(storage, userId, daten) {
  if (!storage || !userId) return false;
  return sicher(() => {
    storage.setItem(vergleichKey(userId), JSON.stringify(daten));
    return true;
  }, false) || false;
}

/** Alle Schluessel eines Speichers — Storage-API zuerst, sonst Object.keys. */
function alleSchluessel(storage) {
  const anzahl = sicher(() => Number(storage.length), 0) || 0;
  if (typeof storage.key === "function" && anzahl > 0) {
    const liste = [];
    for (let i = 0; i < anzahl; i += 1) {
      const k = sicher(() => storage.key(i));
      if (k) liste.push(k);
    }
    if (liste.length) return liste;
  }
  return sicher(() => Object.keys(storage), []) || [];
}

/**
 * ALLE gespeicherten Vergleichsstände entfernen (jedes Konto). Wird beim
 * Abmelden und bei jedem 401 aufgerufen — im Zweifel lieber ein Ergebnis
 * verlieren als es dem nächsten Nutzer zeigen.
 */
export function vergleichLeeren(storage) {
  if (!storage) return 0;
  const keys = alleSchluessel(storage);
  let entfernt = 0;
  for (const k of keys) {
    if (k === VERGLEICH_PRAEFIX || k.startsWith(`${VERGLEICH_PRAEFIX}:`)) {
      sicher(() => storage.removeItem(k));
      entfernt += 1;
    }
  }
  return entfernt;
}

/**
 * Persönliche Oberflächen-Einstellung (Portal-Schalter, Filter-Automatik):
 * je Konto, damit zwei Sucher am selben PC sich nicht gegenseitig umstellen.
 */
export function einstellungKey(name, userId) {
  return `${name}:${userId || "unbekannt"}`;
}

export function einstellungLesen(storage, name, userId, standard) {
  if (!storage) return standard;
  const wert = sicher(() => storage.getItem(einstellungKey(name, userId)));
  if (wert === null || wert === undefined) return standard;
  return wert === "1";
}

export function einstellungSchreiben(storage, name, userId, wert) {
  if (!storage || !userId) return;
  sicher(() => storage.setItem(einstellungKey(name, userId), wert ? "1" : "0"));
}
