// AutoSchnell Helfer — laeuft auf mobile.de, AutoScout24 und Kleinanzeigen (isolierter Bereich der
// Erweiterung, die Portalseite sieht davon nichts).
//
// Inserat offen  -> Seite (gzip) an AutoSchnell, Box mit Fahrzeug, Ampel, Hinweisen, "Kaufvertrag".
// Vergleichsseite, die der Helfer selbst geoeffnet hat -> genau einmal an AutoSchnell (Platz + Ampel).
// Seit 2.3.0 auch die Vergleichsseite des Windows-Programms (dasselbe Konto): Box mit Auto + Ampel hier.
// mobile.de und AutoScout24 wechseln Seiten oft ohne Neuladen: die Adresse wird beobachtet; nach einem
// solchen Wechsel steht das neue Inserat nicht im Quelltext — dann wird die Seite einmal frisch geholt
// (normaler Seitenaufruf im Browser des Nutzers).

(function () {
  "use strict";
  if (window.top !== window) return;
  const A = globalThis.AutoSchnell;
  if (!A || A.portalLaeuft) return;
  A.portalLaeuft = true;

  // ---------------------------------------------------------------- Box (geschlossenes Shadow-DOM)
  const HOST_ID = "autoschnell-helfer-box";
  let wurzel = null;
  let zustand = null;   // { kennung, phase, antwort, marktlage, text, ... }
  let zugeklappt = false;

  function el(tag, klasse, text) {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  function wurzelHolen() {
    let host = document.getElementById(HOST_ID);
    if (host && wurzel) return wurzel;
    if (host) host.remove();
    host = document.createElement("div");
    host.id = HOST_ID;
    // oberste Ebene: Portale legen Cookie-Fenster mit eigener Ebene ueber die Seite (E2E 04.10.2026)
    host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;";
    wurzel = host.attachShadow({ mode: "closed" });
    (document.body || document.documentElement).appendChild(host);
    return wurzel;
  }

  function boxWeg() {
    const host = document.getElementById(HOST_ID);
    if (host) host.remove();
    wurzel = null;
  }

  const STIL = `
    :host{all:initial}
    .box{width:320px;max-width:calc(100vw - 32px);max-height:calc(100vh - 32px);overflow:auto;background:#fff;color:#111827;
      border:1px solid #e5e7eb;border-top:4px solid #ff3b30;border-radius:14px;box-shadow:0 12px 32px rgba(0,0,0,.22);
      font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
    .kopf{display:flex;align-items:center;gap:8px;padding:10px 12px;cursor:pointer;user-select:none}
    .marke{flex:1;font-weight:800;font-size:13px;letter-spacing:.2px}
    .marke span{color:#ff3b30}
    .knopf-klein{border:0;background:transparent;color:#6b7280;font-size:16px;line-height:1;cursor:pointer;padding:2px 6px;border-radius:6px}
    .knopf-klein:hover{background:#f3f4f6;color:#111827}
    .inhalt{padding:0 12px 12px}
    .fz{font-weight:600;margin-bottom:2px}
    .klein{color:#6b7280;font-size:12px}
    .zeile{display:flex;gap:8px;align-items:flex-start;margin:8px 0}
    .punkt{flex:none;width:12px;height:12px;border-radius:50%;margin-top:3px;box-shadow:0 0 0 2px #fff,0 0 0 3px currentColor}
    .gruen{color:#16a34a;background:#16a34a}.gelb{color:#d97706;background:#d97706}.rot{color:#dc2626;background:#dc2626}
    .grau{color:#9ca3af;background:#9ca3af}
    .ampeltext{font-weight:600}
    .portal{font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.4px}
    ul{margin:6px 0 0;padding-left:18px}
    li{margin:2px 0}
    .knoepfe{display:flex;gap:8px;margin-top:10px}
    .knopf{flex:1;border:0;border-radius:999px;padding:8px 10px;font:600 13px/1.2 inherit;cursor:pointer}
    .haupt{background:#ff3b30;color:#fff}.haupt:hover{background:#e6352b}
    .neben{background:#f3f4f6;color:#111827}.neben:hover{background:#e5e7eb}
    .fehler{color:#b91c1c}
    .spin{display:inline-block;width:11px;height:11px;border:2px solid #fecaca;border-top-color:#ff3b30;border-radius:50%;
      animation:s 1s linear infinite;vertical-align:-1px;margin-right:6px}
    @keyframes s{to{transform:rotate(360deg)}}
    hr{border:0;border-top:1px solid #f3f4f6;margin:8px 0}
    .umgerechnet{margin-top:3px;font-size:12px;color:#111827;background:#f9fafb;border-radius:6px;padding:4px 6px}
    .aufklapp{all:unset;cursor:pointer;display:block;margin-top:4px;font-size:12px;color:#b45309}
    .aufklapp:hover{text-decoration:underline}
    .liste{margin:4px 0 0;padding-left:16px;max-height:140px;overflow:auto;font-size:11px;color:#4b5563}
  `;

  function fahrzeugZeile(f) {
    const teile = [[f.marke, f.modell].filter(Boolean).join(" ")];
    if (f.ez_jahr) teile.push("EZ " + (f.ez_monat ? String(f.ez_monat).padStart(2, "0") + "/" : "") + f.ez_jahr);
    if (typeof f.kilometer === "number") teile.push(f.kilometer.toLocaleString("de-DE") + " km");
    if (f.ps) teile.push(f.ps + " PS");
    return teile.filter(Boolean).join(" · ");
  }

  function lageZeile(name, lage, wartet, ohneText) {
    const z = el("div", "zeile");
    const farbe = lage ? (lage.ampel || "grau") : "grau";
    z.appendChild(el("span", "punkt " + farbe));
    const t = el("div");
    t.appendChild(el("div", "portal", name));
    if (!lage) {
      const w = el("div", "klein");
      if (wartet) {
        w.appendChild(el("span", "spin"));
        w.appendChild(document.createTextNode("Vergleichsseite wird ausgewertet …"));
      } else {
        w.textContent = ohneText || "Vergleich nicht geöffnet";
      }
      t.appendChild(w);
    } else {
      t.appendChild(el("div", "ampeltext", lage.text || ""));
      if (lage.text_guenstigstes) t.appendChild(el("div", "klein", lage.text_guenstigstes));
      if (typeof lage.mitte === "number") t.appendChild(el("div", "klein", "Mitte aller sauberen Angebote: " + A.euro(lage.mitte)));
      // Wunsch Ahmad 04.10.2026: guenstigstes Angebot auf km und Baujahr des eigenen Autos umgerechnet
      const u = lage.umgerechnet;
      if (u && u.text) {
        const zeile = el("div", "umgerechnet", u.text + (u.text_inserat ? " – " + u.text_inserat : ""));
        zeile.title = u.text_faktoren || "";
        t.appendChild(zeile);
      }
      // ... und Unfallwagen, Export, Neuwagen usw. aussortiert (aufklappbar)
      if (lage.text_aussortiert) {
        const offen = !!(zustand && zustand.offeneListen && zustand.offeneListen[name]);
        const knopf = el("button", "aufklapp", lage.text_aussortiert + (offen ? " ▴" : " ▾"));
        knopf.setAttribute("aria-expanded", offen ? "true" : "false");
        knopf.addEventListener("click", (ev) => {
          ev.stopPropagation();
          zustand.offeneListen = { ...(zustand.offeneListen || {}), [name]: !offen };
          zeichnen();
        });
        t.appendChild(knopf);
        if (offen) {
          const ul = el("ul", "liste");
          for (const a of lage.aussortiert || []) {
            ul.appendChild(el("li", null, A.euro(a.preis) + " · " + a.grund_text + (a.titel ? " – " + a.titel : "")));
          }
          t.appendChild(ul);
        }
      }
    }
    z.appendChild(t);
    return z;
  }

  function bewertungZeile(b) {
    if (!b) return null;
    if (b.portal === "mobile.de" && b.stufe) {
      return "mobile.de-Bewertung: " + b.stufe
        + (typeof b.fair_von === "number" ? " (fairer Preis " + A.euro(b.fair_von).replace(" €", "") + "–" + A.euro(b.fair_bis) + ")" : "");
    }
    if (b.portal === "AutoScout24" && typeof b.mitte === "number") return "AutoScout24-Marktmitte: ca. " + A.euro(b.mitte);
    return null;
  }

  function zeichnen() {
    if (!zustand) { boxWeg(); return; }
    const r = wurzelHolen();
    r.textContent = "";
    const stil = el("style");
    stil.textContent = STIL;
    r.appendChild(stil);
    const box = el("div", "box");
    box.setAttribute("role", "region");
    box.setAttribute("aria-label", "AutoSchnell Helfer");
    const kopf = el("div", "kopf");
    const marke = el("div", "marke");
    marke.appendChild(el("span", null, "Auto"));
    marke.appendChild(document.createTextNode("Schnell"));
    const z = zustand;
    const a = z.antwort;
    if (zugeklappt && a && a.fahrzeug && a.fahrzeug.preis) marke.appendChild(document.createTextNode(" · " + A.euro(a.fahrzeug.preis)));
    kopf.appendChild(marke);
    const klapp = el("button", "knopf-klein", zugeklappt ? "▸" : "▾");
    klapp.title = zugeklappt ? "Aufklappen" : "Zuklappen";
    klapp.setAttribute("aria-label", klapp.title);
    const zu = el("button", "knopf-klein", "×");
    zu.title = "Schließen";
    zu.setAttribute("aria-label", "Schließen");
    kopf.appendChild(klapp);
    kopf.appendChild(zu);
    const umschalten = (ev) => { ev.stopPropagation(); zugeklappt = !zugeklappt; zeichnen(); };
    kopf.addEventListener("click", umschalten);
    klapp.addEventListener("click", umschalten);
    zu.addEventListener("click", (ev) => { ev.stopPropagation(); zustand = null; boxWeg(); });
    box.appendChild(kopf);

    if (!zugeklappt) {
      const inhalt = el("div", "inhalt");
      if (z.phase === "laden") {
        const p = el("div", "klein");
        p.appendChild(el("span", "spin"));
        p.appendChild(document.createTextNode("Inserat wird gelesen …"));
        inhalt.appendChild(p);
      } else if (z.phase === "fehler") {
        inhalt.appendChild(el("div", "fehler", z.text || "Das hat nicht geklappt."));
      } else if (z.phase === "suche") {
        // Vergleichsseite des Windows-Programms: das Auto dazu und wo es hier liegt
        inhalt.appendChild(el("div", "fz", fahrzeugZeile(z.fahrzeug || {})));
        inhalt.appendChild(el("div", "klein", "Preis im Inserat: " + A.euro((z.fahrzeug || {}).preis)));
        inhalt.appendChild(lageZeile(z.portal || "", z.lage, !z.lage && !z.text, z.text));
        inhalt.appendChild(el("div", "klein", "Vergleich aus dem Vergleich-Programm."));
      } else if (a) {
        inhalt.appendChild(el("div", "fz", fahrzeugZeile(a.fahrzeug || {})));
        inhalt.appendChild(el("div", "klein", "Preis im Inserat: " + A.euro((a.fahrzeug || {}).preis)));
        const portale = (a.links || []).map((l) => l.portal);
        const offen = z.geoeffnet > 0 || z.schonOffen;
        // Wunsch Ahmad 04.10.2026: das Programm hat die Vergleiche schon offen — die Ampel kommt per Direktabruf
        const ohneText = z.vomProgramm && !offen ? "Im Vergleich-Programm geöffnet" : "";
        for (const [name, schluessel] of [["mobile.de", "mobile"], ["AutoScout24", "autoscout"]]) {
          if (portale.includes(name)) inhalt.appendChild(lageZeile(name, (z.marktlage || {})[schluessel], offen, ohneText));
        }
        const bw = bewertungZeile(a.portal_bewertung);
        if (bw) inhalt.appendChild(el("div", "klein", bw));
        const hinweise = (a.verhandlung || []).slice(0, 5);
        if (hinweise.length) {
          inhalt.appendChild(el("hr"));
          inhalt.appendChild(el("div", "portal", "Für die Verhandlung"));
          const ul = el("ul");
          for (const h of hinweise) ul.appendChild(el("li", null, h));
          inhalt.appendChild(ul);
        }
        for (const h of (a.hinweise || []).slice(0, 2)) inhalt.appendChild(el("div", "klein", h));
        if (z.ausVergleich && !offen) inhalt.appendChild(el("div", "klein", "Aus einer Vergleichsseite geöffnet – Vergleiche nur auf Knopfdruck."));
        else if (z.vomProgramm && !offen) inhalt.appendChild(el("div", "klein", "Das Vergleich-Programm hat dieses Auto gerade verglichen – hier nur auf Knopfdruck."));
        if (z.meldung) inhalt.appendChild(el("div", "klein", z.meldung));
        const knoepfe = el("div", "knoepfe");
        const vertrag = el("button", "knopf haupt", "Kaufvertrag");
        vertrag.title = "Auto sofort in AutoSchnell öffnen – alle Daten sind schon da";
        vertrag.addEventListener("click", async (ev) => {
          if (!ev.isTrusted) return;
          const r2 = await A.senden({ typ: "vertrag", kennung: z.kennung });
          if (r2 && r2.protokoll) {
            // Installierte App ist zu: per Link-Typ web+autoschnell: starten (noch im Klick, sonst blockt der Browser)
            const a = document.createElement("a");
            a.href = r2.protokoll;
            a.style.display = "none";
            (document.body || document.documentElement).appendChild(a);
            a.click();
            a.remove();
            z.meldung = "AutoSchnell-App wird geöffnet …";
            zeichnen();
            const r3 = await A.senden({ typ: "app_start_pruefen", kennung: z.kennung });
            z.meldung = r3 && r3.weg === "webseite" ? "Keine AutoSchnell-App gefunden – Webseite geöffnet." : "";
            zeichnen();
            return;
          }
          if (!r2 || r2.fehler) { z.meldung = "AutoSchnell konnte nicht geöffnet werden."; zeichnen(); }
        });
        knoepfe.appendChild(vertrag);
        if ((a.links || []).length) {
          // Wunsch Ahmad 04.10.2026: "Vergleich öffnen" immer drücken können — wie "Vergleichen" im Programm
          const vergl = el("button", "knopf neben", "Vergleich öffnen");
          vergl.title = "Vergleichsseiten mit euren AutoSchnell-Einstellungen öffnen";
          vergl.addEventListener("click", async (ev) => {
            if (!ev.isTrusted) return;
            await vergleichOeffnen();
          });
          knoepfe.appendChild(vergl);
        }
        inhalt.appendChild(knoepfe);
      }
      box.appendChild(inhalt);
    }
    r.appendChild(box);
  }

  // ---------------------------------------------------------------- Inserat
  let aktuelleKennung = null;

  async function frischHolen(url) {
    try {
      const r = await fetch(url, { credentials: "include", cache: "no-store" });
      return r.ok ? await r.text() : null;
    } catch (e) {
      return null;
    }
  }

  async function inserat(kennung, ersterAufruf) {
    zustand = { kennung, phase: "laden" };
    zeichnen();
    // Erster Aufruf: die geladene Seite enthaelt das Inserat. Nach einem Wechsel ohne Neuladen: frisch holen.
    let html = ersterAufruf ? document.documentElement.outerHTML : await frischHolen(location.href);
    if (!html) html = document.documentElement.outerHTML;
    let antwort = await A.senden({ typ: "inserat", kennung, url: location.href, seite: await A.packen(html) });
    if (antwort && antwort.fehler === "seite" && ersterAufruf) {
      const frisch = await frischHolen(location.href);
      if (frisch && kennung === aktuelleKennung) {
        antwort = await A.senden({ typ: "inserat", kennung, url: location.href, seite: await A.packen(frisch) });
      }
    }
    if (kennung !== aktuelleKennung) return;          // inzwischen weitergeklickt
    if (!antwort) {
      zustand = { kennung, phase: "fehler", text: "Der AutoSchnell Helfer antwortet nicht – Seite neu laden." };
    } else if (antwort.fehler) {
      zustand = { kennung, phase: "fehler", text: antwort.text || "Das Inserat konnte nicht gelesen werden." };
    } else {
      // Eine schon eingetroffene Ampel (direkt geholte Vergleichsseite) nicht wieder wegwerfen
      const schon = (zustand && zustand.kennung === kennung && zustand.marktlage) || {};
      zustand = { kennung, phase: "fertig", antwort: antwort.antwort, geoeffnet: antwort.geoeffnet,
                  schonOffen: antwort.schonOffen, ausVergleich: antwort.ausVergleich, vomProgramm: antwort.vomProgramm,
                  marktlage: { ...schon, ...(antwort.marktlage || {}) } };
    }
    zeichnen();
  }

  /** "Vergleich öffnen" — aus der Box oder aus dem Fenster am AutoSchnell-Symbol (auch bei zugemachter Box). */
  async function vergleichOeffnen() {
    const kennung = aktuelleKennung;
    if (!kennung) return { fehler: "kein_inserat" };
    const r = await A.senden({ typ: "vergleiche_oeffnen", kennung });
    if (r && r.geoeffnet && zustand && zustand.kennung === kennung) {
      zustand.geoeffnet = r.geoeffnet;
      zeichnen();
    }
    return r || { fehler: "intern" };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg && msg.typ === "marktlage" && zustand && msg.kennung === zustand.kennung) {
      zustand.marktlage = { ...(zustand.marktlage || {}), [msg.portal]: msg.lage };
      zeichnen();
    }
    if (msg && msg.typ === "vergleich_oeffnen") {             // Knopf im Fenster am Symbol (popup.js)
      vergleichOeffnen().then(sendResponse, () => sendResponse({ fehler: "intern" }));
      return true;
    }
    return false;
  });

  // ---------------------------------------------------------------- Vergleichsseite (eigene Tabs + die des Programms)
  function istVergleichsseite(href) {
    try {
      const u = new URL(href);
      if (u.hostname === "suchen.mobile.de") return u.pathname.startsWith("/fahrzeuge/search.html");
      return /^www\.autoscout24\.(de|at|ch)$/.test(u.hostname) && u.pathname.startsWith("/lst");
    } catch (e) {
      return false;
    }
  }

  let sucheGeschickt = false;
  async function vergleichsseite() {
    if (sucheGeschickt) return;
    const bereit = await A.senden({ typ: "suche_bereit", url: location.href });
    if (!bereit || !bereit.senden) return;
    sucheGeschickt = true;
    const programm = bereit.programm;
    if (programm && !aktuelleKennung) {
      zustand = { phase: "suche", fahrzeug: programm.fahrzeug || {}, portal: programm.portal, lage: null };
      zeichnen();
    }
    const seite = await A.packen(document.documentElement.outerHTML);
    const r = await A.senden({ typ: "suche", url: location.href, seite });
    if (programm && zustand && zustand.phase === "suche") {
      if (r && r.lage) zustand.lage = r.lage;
      else zustand.text = (r && r.text) || "Die Vergleichsseite konnte nicht ausgewertet werden.";
      zeichnen();
    }
  }

  // ---------------------------------------------------------------- Adresse beobachten
  function pruefen(ersterAufruf) {
    const kennung = A.inseratKennung(location.href);
    if (kennung) {
      if (kennung !== aktuelleKennung) {
        aktuelleKennung = kennung;
        inserat(kennung, ersterAufruf);
      }
      return;
    }
    if (aktuelleKennung) {                 // vom Inserat weg (ohne Neuladen)
      aktuelleKennung = null;
      zustand = null;
      boxWeg();
    }
    if (ersterAufruf && istVergleichsseite(location.href)) vergleichsseite();
  }

  pruefen(true);
  let letzte = location.href;
  setInterval(() => {
    if (location.href !== letzte) {
      letzte = location.href;
      pruefen(false);
    }
  }, 700);
})();
