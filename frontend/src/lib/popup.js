/**
 * Öffnet eine URL in einem Popup-Fenster.
 * Falls der Browser den Popup blockiert → Fallback neuer Tab.
 */
export function openInPopup(url, name = "filterWindow", width = 1280, height = 1000) {
  if (!url) return null;
  const isSmallScreen = typeof window !== "undefined" && window.innerWidth < 900;
  if (isSmallScreen) {
    window.open(url, "_blank", "noopener,noreferrer");
    return null;
  }
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
  let popup = null;
  try { popup = window.open(url, name, features); } catch { popup = null; }
  if (!popup || popup.closed || typeof popup.closed === "undefined") {
    window.open(url, "_blank", "noopener,noreferrer");
    return null;
  }
  try { popup.opener = null; } catch { /* ignore */ }
  try { popup.focus(); } catch { /* ignore */ }
  return popup;
}

/**
 * Öffnet mehrere URLs auf einmal — zuverlässig ohne Popup-Blocker.
 *
 * Chrome erlaubt nur EINEN window.open(…, "popup=yes") pro User-Geste.
 * Bei mehr als einer URL öffnen wir deshalb als normale Tabs (kein
 * "popup=yes"), die werden vom Browser nie geblockt wenn sie synchron
 * im selben Click-Handler aufgerufen werden.
 */
export function openMultiple(urls) {
  // urls: Array von { url, name }
  const valid = urls.filter((u) => u?.url);
  if (valid.length === 0) return;
  if (valid.length === 1) {
    // Einzeln → Popup
    openInPopup(valid[0].url, valid[0].name || "filterWindow");
    return;
  }
  // Mehrere → Tabs (synchron, kein Popup-Feature → nicht geblockt)
  valid.forEach(({ url, name }) => {
    try {
      window.open(url, name || "_blank");
    } catch { /* ignore */ }
  });
}
