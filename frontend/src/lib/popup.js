/**
 * Runde 22 (11.09.2026): Fenster-Oeffnen mit Blocker-Erkennung.
 *
 * Chrome und Edge erlauben je Nutzer-Geste nur EIN neues Fenster/Tab —
 * window.open verbraucht die Aktivierung, jeder weitere Aufruf im selben
 * Klick wird still blockiert (auch "_blank"-Tabs). Vorher oeffnete
 * "Filter öffnen" deshalb nur mobile.de, AutoScout24 ging verloren. Und
 * mit "noopener" liefert window.open IMMER null — ein Blockieren war so
 * gar nicht erkennbar. Darum hier nie "noopener", sondern (OWASP-Reihen-
 * folge) leeres Fenster oeffnen, opener kappen, erst dann navigieren. Die
 * Funktionen melden zurueck, was blockiert wurde; lib/filterOeffnen.js
 * zeigt dafuer einen Hinweis mit Knopf (= neue Geste) an.
 */

/**
 * Oeffnet (oder wiederverwendet) einen BENANNTEN Tab und navigiert ihn.
 * Benannt, damit der naechste Vergleich denselben Tab nutzt, statt neue
 * Tabs anzuhaeufen. Rueckgabe: Fenster oder null (= vom Browser blockiert).
 */
function tabOeffnen(url, name) {
  let w = null;
  try { w = window.open("", name); } catch { w = null; }
  if (!w || w.closed || typeof w.closed === "undefined") return null;
  // opener kappen, BEVOR mobile.de/AutoScout geladen wird — setzt das
  // "disowned"-Flag und ueberlebt die Navigation.
  try { w.opener = null; } catch { /* Fremd-Origin (wiederverwendeter Tab): Flag ist vom ersten Oeffnen gesetzt */ }
  try {
    w.location.href = url;
  } catch {
    // Ersatz: benanntes Ziel direkt navigieren (bestehendes Fenster -> kein neues Popup)
    try { w = window.open(url, name) || w; } catch { /* ignore */ }
  }
  try { w.focus(); } catch { /* ignore */ }
  return w;
}

// ---------------------------------------------------------------------
// Fenster daneben / zweiter Bildschirm (15.09.2026, Wunsch Ahmad): Laeuft die
// App auf einem von zwei Bildschirmen, sollen die Filter-Fenster nicht UEBER
// der App liegen, sondern daneben — auf dem anderen Bildschirm, wenn der
// Browser das erlaubt (Window-Management-Berechtigung), sonst neben dem
// App-Fenster, soweit Platz ist.
// ---------------------------------------------------------------------
let danebenAktiv = false;
let bildschirme = null;            // ScreenDetails (Chrome/Edge) oder null

export function fensterDanebenSetzen(an) { danebenAktiv = !!an; }
export function fensterDanebenAktiv() { return danebenAktiv; }

/** Im Klick aufrufen: fragt (einmalig) die Bildschirm-Berechtigung des Browsers ab. */
export async function zweitenBildschirmAnfragen() {
  if (typeof window === "undefined" || typeof window.getScreenDetails !== "function") {
    return { ok: false, grund: "browser" };
  }
  try {
    bildschirme = await window.getScreenDetails();
    return { ok: true, anzahl: bildschirme?.screens?.length || 1 };
  } catch {
    bildschirme = null;
    return { ok: false, grund: "verweigert" };
  }
}

function andererBildschirm() {
  const liste = bildschirme?.screens;
  if (!liste || liste.length < 2) return null;
  const aktuell = bildschirme.currentScreen;
  return liste.find((s) => s !== aktuell) || null;
}

/**
 * Platz fuer das Filter-Fenster: { left, top, width, height }.
 * daneben + anderer Bildschirm: der ganze andere Bildschirm.
 * daneben ohne zweiten Bildschirm: rechts neben der App, wenn sie links
 * laeuft (sonst links), sofern dort mindestens 640 px frei sind.
 * Sonst (bisher): zentriert ueber dem App-Fenster.
 */
export function fensterPlatz({ width, height, app, screen, anderer = null, daneben = false }) {
  const availW = screen?.availWidth || 1280;
  const availH = screen?.availHeight || 900;
  const availLeft = screen?.availLeft || 0;
  const availTop = screen?.availTop || 0;
  if (daneben && anderer) {
    return {
      left: anderer.availLeft ?? 0, top: anderer.availTop ?? 0,
      width: anderer.availWidth || width, height: anderer.availHeight || height,
    };
  }
  const w0 = Math.min(width, Math.floor(availW * 0.92));
  const h0 = Math.min(height, Math.floor(availH * 0.92));
  if (daneben) {
    const MIN = 640;
    const platzLinks = app.screenX - availLeft;
    const platzRechts = availLeft + availW - (app.screenX + app.outerWidth);
    const appLinks = app.screenX + app.outerWidth / 2 < availLeft + availW / 2;
    const seite = appLinks
      ? (platzRechts >= MIN ? "rechts" : platzLinks >= MIN ? "links" : null)
      : (platzLinks >= MIN ? "links" : platzRechts >= MIN ? "rechts" : null);
    if (seite) {
      const w = Math.min(w0, seite === "rechts" ? platzRechts : platzLinks);
      const h = Math.min(height, availH);
      return {
        left: seite === "rechts" ? app.screenX + app.outerWidth : app.screenX - w,
        top: Math.max(availTop, app.screenY), width: w, height: h,
      };
    }
  }
  return {
    width: w0, height: h0,
    left: Math.max(0, app.screenX + Math.floor((app.outerWidth - w0) / 2)),
    top: Math.max(0, app.screenY + Math.floor((app.outerHeight - h0) / 2)),
  };
}

function aktuellerPlatz(width, height) {
  const screenW = window.screen?.availWidth || 1280;
  const screenH = window.screen?.availHeight || 900;
  const app = {
    screenX: window.screenX || 0, screenY: window.screenY || 0,
    outerWidth: window.outerWidth || screenW, outerHeight: window.outerHeight || screenH,
  };
  return fensterPlatz({ width, height, app, screen: window.screen,
                        anderer: danebenAktiv ? andererBildschirm() : null, daneben: danebenAktiv });
}

function featuresAus(platz) {
  return [
    `width=${platz.width}`, `height=${platz.height}`, `left=${platz.left}`, `top=${platz.top}`,
    "menubar=no", "toolbar=no", "location=yes",
    "status=no", "scrollbars=yes", "resizable=yes", "popup=yes",
  ].join(",");
}

/**
 * Öffnet eine URL in einem Popup-Fenster (kleine Bildschirme: benannter Tab).
 * Blockiert der Browser das Popup → Ersatz benannter Tab.
 * Rueckgabe: Fenster oder null (= blockiert).
 */
export function openInPopup(url, name = "filterWindow", width = 1280, height = 1000, platz = null) {
  if (!url) return null;
  const isSmallScreen = typeof window !== "undefined" && window.innerWidth < 900;
  if (isSmallScreen) return tabOeffnen(url, name);
  const features = featuresAus(platz || aktuellerPlatz(width, height));
  // Bewusst KEIN "noopener" in den Features: damit liefert window.open null,
  // die Blocker-Erkennung unten hielte jeden Aufruf fuer geblockt, das
  // benannte Fenster wuerde beim naechsten Klick nicht wiederverwendet und
  // focus() entfiele. Stattdessen (OWASP-Reihenfolge): leeres Fenster
  // oeffnen, opener kappen — das setzt das "disowned"-Flag des Browsing
  // Context und ueberlebt die Navigation zu mobile.de/AutoScout — und erst
  // dann navigieren.
  let popup = null;
  try { popup = window.open("", name, features); } catch { popup = null; }
  if (!popup || popup.closed || typeof popup.closed === "undefined") {
    // Runde 22: manche Blocker lassen Tabs durch, aber keine Popups.
    return tabOeffnen(url, name);
  }
  try { popup.opener = null; } catch { /* Fenster zeigt schon Fremd-Origin: Flag ist vom ersten Oeffnen gesetzt */ }
  try {
    popup.location.href = url;
  } catch {
    // Ersatz: benanntes Fenster direkt navigieren (existiert schon -> kein neues Popup)
    try { popup = window.open(url, name) || popup; } catch { /* ignore */ }
  }
  try { popup.focus(); } catch { /* ignore */ }
  return popup;
}

/**
 * Öffnet mehrere URLs ({ url, name, ... }) und gibt die Liste der vom
 * Browser BLOCKIERTEN Eintraege zurueck ([] = alle offen).
 *
 * Runde 22 (11.09.2026): Der alte Kommentar hier ("neue Tabs werden nie
 * geblockt") war falsch — Chrome/Edge lassen je Geste nur EIN Fenster zu,
 * der zweite Aufruf scheitert still. Hat der Nutzer Pop-ups fuer die Seite
 * erlaubt, gehen alle auf einmal auf; sonst meldet die Rueckgabe den Rest,
 * damit ein Knopf (neue Geste) ihn nachholen kann.
 */
export function openMultiple(urls) {
  const valid = (urls || []).filter((u) => u?.url);
  if (valid.length === 0) return [];
  if (valid.length === 1) {
    // Einzeln → Popup mit Namen (Wiederverwendung bei erneutem Klick)
    return openInPopup(valid[0].url, valid[0].name || "filterWindow") ? [] : [valid[0]];
  }
  const anderer = danebenAktiv ? andererBildschirm() : null;
  if (anderer) {
    // Zweiter Bildschirm: die Portale als Fenster uebereinander dort — das
    // zweite kann der Pop-up-Blocker halten (Hinweis mit Knopf wie bisher).
    const hoehe = Math.floor((anderer.availHeight || 900) / valid.length);
    return valid.filter((e, i) => !openInPopup(e.url, e.name || `filterWindow${i + 1}`, 1280, hoehe, {
      left: anderer.availLeft ?? 0, top: (anderer.availTop ?? 0) + i * hoehe,
      width: anderer.availWidth || 1280, height: hoehe,
    }));
  }
  // Mehrere → je ein benannter Tab (Wiederverwendung statt Tab-Stapel)
  return valid.filter((e, i) => !tabOeffnen(e.url, e.name || `filterWindow${i + 1}`));
}
