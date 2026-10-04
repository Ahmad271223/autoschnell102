// AutoSchnell Helfer — gemeinsame Hilfen der Seiten-Skripte (portal.js).
// Liegt im selben isolierten Bereich wie das jeweilige Skript; die Portalseite sieht davon nichts.

var AutoSchnell = globalThis.AutoSchnell || (globalThis.AutoSchnell = {});

/** Seite gzip-packen und als base64 liefern (ein mobile.de-Inserat: ~1 MB -> ~200 KB). */
AutoSchnell.packen = async function packen(text) {
  const strom = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
  const bytes = new Uint8Array(await new Response(strom).arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
};

/** Kennung des Inserats in der Adresse (wie listing_identity auf dem Server) oder null. */
AutoSchnell.inseratKennung = function inseratKennung(href) {
  let u;
  try { u = new URL(href); } catch (e) { return null; }
  const host = u.hostname.toLowerCase();
  if (host === "suchen.mobile.de") {
    const id = u.searchParams.get("id") || (/\/(\d{6,})\.html$/.exec(u.pathname) || [])[1];
    return id && /^\d{6,20}$/.test(id) ? "mobile:" + id : null;
  }
  if (/^www\.autoscout24\.(de|at|ch)$/.test(host) && u.pathname.toLowerCase().includes("/angebote/")) {
    const m = /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i.exec(u.pathname);
    return m ? "autoscout24:" + m[1].toLowerCase() : null;
  }
  if (/(^|\.)kleinanzeigen\.de$/.test(host)) {
    const m = /\/s-anzeige\/(?:[^/]+\/)?(\d{6,})/.exec(u.pathname);
    return m ? "kleinanzeigen:" + m[1] : null;
  }
  return null;
};

/** Nachricht an den Hintergrund; nie eine Ausnahme (Erweiterung neu geladen -> null). */
AutoSchnell.senden = function senden(nachricht) {
  return new Promise((fertig) => {
    try {
      chrome.runtime.sendMessage(nachricht, (antwort) => {
        if (chrome.runtime.lastError) { fertig(null); return; }
        fertig(antwort || null);
      });
    } catch (e) {
      fertig(null);
    }
  });
};

/** Ist die Erweiterung noch erreichbar? Nach einem Update oder "Neu laden" laeuft ein schon geladenes Seiten-Skript
 *  weiter, kann aber nichts mehr senden (Chrome laedt offene Tabs nicht neu) — dann muss die Seite neu geladen werden. */
AutoSchnell.helferDa = function helferDa() {
  try { return !!(chrome.runtime && chrome.runtime.id); } catch (e) { return false; }
};

AutoSchnell.euro = function euro(n) {
  return typeof n === "number" && isFinite(n) ? Math.round(n).toLocaleString("de-DE") + " €" : "–";
};

// Tempo (2.4.0): Diese Datei laeuft schon beim Seitenstart (document_start), portal.js erst, wenn die Seite da ist.
// Auf einem Inserat jetzt schon den Hintergrund wecken und die Verbindung zu AutoSchnell aufbauen lassen — bis die
// Seite geladen ist, laeuft beides, und das Inserat geht ohne Aufwachen und Verbindungsaufbau raus.
if (window.top === window && !AutoSchnell.fruehGemeldet && AutoSchnell.inseratKennung(location.href)) {
  AutoSchnell.fruehGemeldet = true;
  AutoSchnell.senden({ typ: "frueh" });
}
