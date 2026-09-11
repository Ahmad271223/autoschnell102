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

/**
 * Öffnet eine URL in einem Popup-Fenster (kleine Bildschirme: benannter Tab).
 * Blockiert der Browser das Popup → Ersatz benannter Tab.
 * Rueckgabe: Fenster oder null (= blockiert).
 */
export function openInPopup(url, name = "filterWindow", width = 1280, height = 1000) {
  if (!url) return null;
  const isSmallScreen = typeof window !== "undefined" && window.innerWidth < 900;
  if (isSmallScreen) return tabOeffnen(url, name);
  const screenW = window.screen?.availWidth || 1280;
  const screenH = window.screen?.availHeight || 900;
  const w = Math.min(width, Math.floor(screenW * 0.92));
  const h = Math.min(height, Math.floor(screenH * 0.92));
  const left = Math.max(0, (window.screenX || 0) + Math.floor(((window.outerWidth || screenW) - w) / 2));
  const top  = Math.max(0, (window.screenY || 0) + Math.floor(((window.outerHeight || screenH) - h) / 2));
  const features = [
    `width=${w}`, `height=${h}`, `left=${left}`, `top=${top}`,
    "menubar=no", "toolbar=no", "location=yes",
    "status=no", "scrollbars=yes", "resizable=yes", "popup=yes",
  ].join(",");
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
  // Mehrere → je ein benannter Tab (Wiederverwendung statt Tab-Stapel)
  return valid.filter((e, i) => !tabOeffnen(e.url, e.name || `filterWindow${i + 1}`));
}
