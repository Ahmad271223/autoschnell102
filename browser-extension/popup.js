// AutoSchnell Helfer — Fenster am Symbol: verbinden (6-stelliger Code), Status, Automatik, trennen.
"use strict";

const $ = (id) => document.getElementById(id);
const SERVER_STANDARD = "https://app.auto-schnellkauf.de";

function senden(nachricht) {
  return new Promise((fertig) => {
    chrome.runtime.sendMessage(nachricht, (antwort) => fertig(chrome.runtime.lastError ? null : antwort));
  });
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

function datum(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "–" : d.toLocaleDateString("de-DE");
}

function zeigen(st) {
  $("laden").hidden = true;
  $("version").textContent = "Version " + (st && st.version ? st.version : chrome.runtime.getManifest().version)
    + (st && st.server && st.server !== SERVER_STANDARD ? " · Server " + st.server : "");
  if (!st || !st.verbunden) {
    $("verbunden").hidden = true;
    $("getrennt").hidden = false;
    $("grund").hidden = !(st && st.grund);
    $("grund").textContent = (st && st.grund) || "";
    $("server").value = st && st.server && st.server !== SERVER_STANDARD ? st.server : "";
    $("code").focus();
    return;
  }
  $("getrennt").hidden = true;
  $("verbunden").hidden = false;
  $("konto").textContent = st.konto || "–";
  $("name").textContent = st.name || "–";
  $("firma").textContent = st.firma || "–";
  $("abo").textContent = datum(st.abo_bis);
  // 2.6.0 (Pruefung 05.10.2026, Nr. 9): entpackte Erweiterungen aktualisieren sich nicht selbst — sagen, wenn es
  // in AutoSchnell eine neuere Version gibt
  const ich = st.version || chrome.runtime.getManifest().version;
  const neu = st.aktuelle_version && istNeuer(st.aktuelle_version, ich)
    ? `Neue Version ${st.aktuelle_version} verfügbar: in AutoSchnell unter Programme herunterladen, in denselben Ordner `
      + "entpacken (alte Dateien überschreiben) und unter Erweiterungen „Neu laden“ drücken." : "";
  const texte = [st.hinweis, neu].filter(Boolean);
  $("hinweis").hidden = !texte.length;
  $("hinweis").textContent = texte.join(" ");
  $("oeffnen").checked = !(st.einstellungen && st.einstellungen.vergleicheOeffnen === false);
}

async function serverRecht(server) {
  // Test-Server (localhost/127.0.0.1): das Recht dafuer fragt der Browser einmal nach
  if (!server || server === SERVER_STANDARD) return true;
  const muster = server.replace(/:\d+$/, "") + "/*";
  try {
    return await chrome.permissions.request({ origins: [muster] });
  } catch (e) {
    return false;
  }
}

$("formular").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("fehler").hidden = true;
  $("verbinden").disabled = true;
  $("verbinden").textContent = "Verbinde …";
  const server = $("server").value.trim().replace(/\/+$/, "");
  try {
    if (!(await serverRecht(server))) {
      $("fehler").textContent = "Ohne Zugriffsrecht auf den Test-Server geht es nicht.";
      $("fehler").hidden = false;
      return;
    }
    const r = await senden({ typ: "verbinden", code: $("code").value, server: server || SERVER_STANDARD });
    if (!r || r.fehler) {
      $("fehler").textContent = (r && r.fehler) || "Verbinden hat nicht geklappt.";
      $("fehler").hidden = false;
      return;
    }
    zeigen(await senden({ typ: "status" }));
  } finally {
    $("verbinden").disabled = false;
    $("verbinden").textContent = "Verbinden";
  }
});

$("trennen").addEventListener("click", async () => {
  await senden({ typ: "trennen" });
  zeigen(await senden({ typ: "status" }));
});

// Wunsch Ahmad 04.10.2026: "Vergleich öffnen" auch hier — fuer das Inserat im gerade offenen Tab
function anTab(tabId, nachricht) {
  return new Promise((fertig) => {
    try {
      chrome.tabs.sendMessage(tabId, nachricht, (antwort) => fertig(chrome.runtime.lastError ? null : antwort));
    } catch (e) {
      fertig(null);
    }
  });
}

$("vergleich").addEventListener("click", async () => {
  const text = $("vergleichText");
  $("vergleich").disabled = true;
  try {
    let tab = null;
    try { [tab] = await chrome.tabs.query({ active: true, currentWindow: true }); } catch (e) { tab = null; }
    const r = tab ? await anTab(tab.id, { typ: "vergleich_oeffnen" }) : null;
    if (r && r.geoeffnet) {
      text.textContent = r.geoeffnet === 1 ? "1 Vergleich geöffnet." : r.geoeffnet + " Vergleiche geöffnet.";
    } else if (r && r.fehler === "unbekannt") {
      text.textContent = "Das Inserat ist noch nicht gelesen – Seite neu laden und noch einmal drücken.";
    } else if (r && r.fehler !== "kein_inserat" && r.fehler) {
      text.textContent = "Das hat nicht geklappt – Seite neu laden und noch einmal drücken.";
    } else if (r && !r.fehler) {
      text.textContent = "Für dieses Auto gibt es keinen Vergleich (Marke oder Modell unbekannt).";
    } else {
      text.textContent = "Hier ist kein Inserat offen – erst ein Auto auf mobile.de, AutoScout24 oder Kleinanzeigen öffnen.";
    }
    text.hidden = false;
  } finally {
    $("vergleich").disabled = false;
  }
});

$("oeffnen").addEventListener("change", async () => {
  await senden({ typ: "einstellungen", einstellungen: { vergleicheOeffnen: $("oeffnen").checked } });
});

// 2.6.3 (Paket 3): erst sofort aus dem Speicher (Konto, Name, Firma), dann der Server-Stand
senden({ typ: "status", schnell: true }).then((st) => {
  zeigen(st);
  if (st && st.vorlaeufig) senden({ typ: "status" }).then(zeigen);
});
