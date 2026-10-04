// AutoSchnell Helfer — Fenster am Symbol: verbinden (6-stelliger Code), Status, Automatik, trennen.
"use strict";

const $ = (id) => document.getElementById(id);
const SERVER_STANDARD = "https://app.auto-schnellkauf.de";

function senden(nachricht) {
  return new Promise((fertig) => {
    chrome.runtime.sendMessage(nachricht, (antwort) => fertig(chrome.runtime.lastError ? null : antwort));
  });
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
  $("hinweis").hidden = !st.hinweis;
  $("hinweis").textContent = st.hinweis || "";
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

$("oeffnen").addEventListener("change", async () => {
  await senden({ typ: "einstellungen", einstellungen: { vergleicheOeffnen: $("oeffnen").checked } });
});

senden({ typ: "status" }).then(zeigen);
