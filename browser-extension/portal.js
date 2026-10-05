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
  // 2.6.0 (Nr. 24): warum die Vergleiche NICHT von selbst aufgingen (background.js automatik())
  const GRUENDE = {
    aus: "Automatisches Öffnen ist aus (Fenster am AutoSchnell-Symbol) – Vergleiche per Knopf.",
    aus_vergleich: "Aus einer Vergleichsseite geöffnet – Vergleiche nur auf Knopfdruck.",
    programm: "Das Vergleich-Programm hat dieses Auto gerade verglichen – hier nur auf Knopfdruck.",
    neu_geladen: "Seite neu geladen – Vergleiche nur auf Knopfdruck.",
    hintergrund: "Im Hintergrund geöffnet – die Vergleiche gehen auf, sobald du hierher wechselst.",
    gebremst: "Viele Inserate auf einmal geöffnet – hier die Vergleiche per Knopf öffnen.",
  };
  // 2.6.0 (Nr. 18): mit × geschlossene Boxen (je Inserat) bleiben zu, bis die Seite neu geladen wird
  const geschlossen = new Set();
  // ohne Ampel nach so vielen ms: sagen, was los ist (statt endlos "wird ausgewertet") — 2.6.0
  const AMPEL_MS = 12000;
  let wurzel = null;
  let hostEl = null;
  let waechter = null;
  let zustand = null;   // { kennung, phase, antwort, marktlage, lageFehler, lageStart, text, ... }
  let zugeklappt = false;

  function el(tag, klasse, text) {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  function einhaengen() {
    (document.body || document.documentElement).appendChild(hostEl);
  }

  /** 2.6.0: Baut die Seite sich neu auf (React) und wirft dabei fremde Elemente raus, haengt sich die Box sofort
   *  wieder ein. Beobachtet nur die oberste Ebene (html, body) — keine Last bei jeder Aenderung der Seite. */
  function beobachten() {
    if (!waechter) {
      waechter = new MutationObserver(() => {
        if (!hostEl || !zustand) return;
        if (!hostEl.isConnected || (document.body && hostEl.parentNode !== document.body)) einhaengen();
        beobachten();                                  // body evtl. ersetzt: neuen body beobachten
      });
    }
    waechter.disconnect();
    waechter.observe(document.documentElement, { childList: true });
    if (document.body) waechter.observe(document.body, { childList: true });
  }

  function wurzelHolen() {
    if (hostEl && wurzel) {
      if (!hostEl.isConnected) einhaengen();
      return wurzel;
    }
    hostEl = document.createElement("div");
    hostEl.id = HOST_ID;
    // oberste Ebene: Portale legen Cookie-Fenster mit eigener Ebene ueber die Seite (E2E 04.10.2026)
    hostEl.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;";
    wurzel = hostEl.attachShadow({ mode: "closed" });
    einhaengen();
    beobachten();
    return wurzel;
  }

  function boxWeg() {
    if (waechter) waechter.disconnect();
    if (hostEl) hostEl.remove();
    hostEl = null;
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

  function lageZeile(name, lage, wartet, ohneText, fehlerText) {
    const z = el("div", "zeile");
    const farbe = lage ? (lage.ampel || "grau") : "grau";
    z.appendChild(el("span", "punkt " + farbe));
    const t = el("div");
    t.appendChild(el("div", "portal", name));
    if (!lage) {
      const w = el("div", "klein");
      if (wartet && fehlerText) {
        // 2.6.0: nicht endlos drehen — sagen, was los ist und was hilft
        w.textContent = fehlerText;
      } else if (wartet) {
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

  /** 2.6.0: Text statt Kreisel — sofort, wenn die Vergleichsseite im Tab nicht auswertbar war; sonst nach AMPEL_MS
   *  (dann mit dem Grund des Direktabrufs, falls einer kam). Kommt die Ampel spaeter doch, ersetzt sie den Text. */
  function ampelFehler(z, schluessel) {
    const f = (z.lageFehler || {})[schluessel];
    const grund = f ? String(f.text || "").replace(/[.\s]+$/, "") : "";
    const hilfe = "Vergleichs-Tab einmal anklicken oder „Vergleich öffnen“ drücken.";
    if (f && !f.vorlaeufig) return grund + " – " + hilfe;
    if (z.lageAbgelaufen) return (grund || "Noch keine Auswertung") + " – " + hilfe;
    return null;
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
    if (!zustand || geschlossen.has(zustand.kennung || "")) { boxWeg(); return; }
    const r = wurzelHolen();
    // 2.6.0 (Nr. 18): beim Neuzeichnen (Ampel kommt) nicht an den Anfang springen
    const vorige = r.querySelector(".box");
    const scroll = vorige ? vorige.scrollTop : 0;
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
    const umschalten = (ev) => {
      ev.stopPropagation();
      zugeklappt = !zugeklappt;
      // 2.6.0 (Nr. 18): zugeklappt bleibt zugeklappt — auch im naechsten Inserat
      try { if (A.helferDa()) chrome.storage.local.set({ boxZugeklappt: zugeklappt }); } catch (e) { /* egal */ }
      zeichnen();
    };
    kopf.addEventListener("click", umschalten);
    klapp.addEventListener("click", umschalten);
    zu.addEventListener("click", (ev) => {
      ev.stopPropagation();
      geschlossen.add(z.kennung || "");          // 2.6.0 (Nr. 18): kommt fuer dieses Inserat nicht wieder
      zustand = null;
      boxWeg();
    });
    box.appendChild(kopf);

    if (!zugeklappt) {
      const inhalt = el("div", "inhalt");
      if (z.veraltet) {
        // Erweiterung aktualisiert/neu geladen: diese Box erreicht den Helfer nicht mehr
        inhalt.appendChild(el("div", "fehler", "Der AutoSchnell Helfer wurde aktualisiert. Bitte die Seite neu laden."));
        const knoepfe = el("div", "knoepfe");
        const neu = el("button", "knopf haupt", "Seite neu laden");
        neu.addEventListener("click", (ev) => { if (ev.isTrusted) location.reload(); });
        knoepfe.appendChild(neu);
        inhalt.appendChild(knoepfe);
      } else if (z.phase === "doppelt") {
        const versionen = [chrome.runtime.getManifest().version, ...A.andere.values()].filter(Boolean);
        inhalt.appendChild(el("div", "fehler", "Der AutoSchnell Helfer ist zweimal installiert"
          + (versionen.length > 1 ? " (Versionen " + versionen.join(" und ") + ")" : "") + "."));
        inhalt.appendChild(el("div", "klein", "Bitte in edge://extensions bzw. chrome://extensions die ältere Version "
          + "entfernen und diese Seite neu laden. Bis dahin öffnet der Helfer nichts, damit nichts doppelt passiert."));
      } else if (z.phase === "laden") {
        const p = el("div", "klein");
        p.appendChild(el("span", "spin"));
        p.appendChild(document.createTextNode("Inserat wird gelesen …"));
        inhalt.appendChild(p);
      } else if (z.phase === "fehler") {
        // 2.6.0 (Nr. 24): "nicht verbunden" ruhig in Grau statt als rote Fehlerbox auf jedem Inserat
        inhalt.appendChild(el("div", z.nichtVerbunden ? "klein" : "fehler", z.text || "Das hat nicht geklappt."));
        if (!z.nichtVerbunden && z.kennung) {
          // 2.6.0 (Nr. 5): nach 429/5xx/Zeitueberschreitung nicht nur "Seite neu laden"
          const knoepfe = el("div", "knoepfe");
          const nochmal = el("button", "knopf neben", "Erneut versuchen");
          nochmal.addEventListener("click", (ev) => {
            if (ev.isTrusted && z.kennung === aktuelleKennung) inserat(z.kennung, true);
          });
          knoepfe.appendChild(nochmal);
          inhalt.appendChild(knoepfe);
        }
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
        const ohneText = offen ? "" : z.vomProgramm ? "Im Vergleich-Programm geöffnet"
          : z.automatik === "hintergrund" ? "Öffnet, sobald du hierher wechselst" : "";
        for (const [name, schluessel] of [["mobile.de", "mobile"], ["AutoScout24", "autoscout"]]) {
          if (portale.includes(name)) {
            inhalt.appendChild(lageZeile(name, (z.marktlage || {})[schluessel], offen, ohneText, ampelFehler(z, schluessel)));
          }
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
        // 2.6.0 (Nr. 24): immer sagen, WARUM die Vergleiche nicht von selbst aufgingen
        const grund = offen ? "" : GRUENDE[z.automatik] || (z.ausVergleich ? GRUENDE.aus_vergleich : z.vomProgramm ? GRUENDE.programm : "");
        if (grund) inhalt.appendChild(el("div", "klein", grund));
        if (z.neueVersion) {
          inhalt.appendChild(el("div", "klein", `Neue Version ${z.neueVersion} des Helfers verfügbar – in AutoSchnell unter Programme.`));
        }
        if (z.meldung) inhalt.appendChild(el("div", "klein", z.meldung));
        if (z.webseiteAnbieten) {
          const knoepfe3 = el("div", "knoepfe");
          const web = el("button", "knopf neben", "Webseite öffnen");
          web.title = "Keine AutoSchnell-App installiert? Dann AutoSchnell als Webseite öffnen (merkt sich der Helfer).";
          web.addEventListener("click", (ev) => { if (ev.isTrusted) webseiteStatt(z); });
          knoepfe3.appendChild(web);
          inhalt.appendChild(knoepfe3);
        }
        if (z.protokollWartet) {
          // 2.6.0 (Nr. 19): nach dem Nachlesen ist die Klick-Erlaubnis des Browsers abgelaufen — ein zweiter Klick
          const knoepfe2 = el("div", "knoepfe");
          const jetzt = el("button", "knopf haupt", "In der AutoSchnell-App öffnen");
          jetzt.addEventListener("click", (ev) => { if (ev.isTrusted) appStarten(z, z.protokollWartet); });
          knoepfe2.appendChild(jetzt);
          inhalt.appendChild(knoepfe2);
        }
        const knoepfe = el("div", "knoepfe");
        const vertrag = el("button", "knopf haupt", "Kaufvertrag");
        vertrag.title = "Auto sofort in AutoSchnell öffnen – alle Daten sind schon da";
        vertrag.addEventListener("click", async (ev) => {
          if (!ev.isTrusted) return;
          if (!A.helferDa()) { veraltet(); return; }
          z.meldung = "AutoSchnell wird geöffnet …";
          z.protokollWartet = "";
          z.webseiteAnbieten = false;
          zeichnen();
          let r2 = await A.senden({ typ: "vertrag", kennung: z.kennung });
          let nachgelesen = false;
          // Helfer kennt das Inserat nicht mehr (Browser neu gestartet, lange offen): nachlesen und noch einmal
          if (r2 && r2.fehler === "unbekannt") {
            const nach = await nachlesen(z.kennung);
            nachgelesen = true;
            r2 = nach && nach.antwort ? await A.senden({ typ: "vertrag", kennung: z.kennung }) : (nach || r2);
          }
          if (r2 && r2.protokoll) {
            if (nachgelesen) {
              // 2.6.0 (Nr. 19): der Browser startet die App nur direkt nach einem Klick — nach dem Nachlesen ist das
              // vorbei, also einen zweiten Klick anbieten statt still zu scheitern
              z.protokollWartet = r2.protokoll;
              z.meldung = "Inserat neu gelesen – bitte noch einmal klicken:";
              zeichnen();
              return;
            }
            await appStarten(z, r2.protokoll);
            return;
          }
          if (!A.helferDa()) { veraltet(); return; }
          z.meldung = r2 && r2.weg === "app_neu_laden"
            ? "Die AutoSchnell-App ist offen, kennt den aktualisierten Helfer aber noch nicht – dort einmal neu laden "
              + "(F5), dann hier noch einmal „Kaufvertrag“ drücken."
            : !r2 || r2.fehler
              ? (r2 && r2.text) || "AutoSchnell konnte nicht geöffnet werden – Seite neu laden und noch einmal drücken."
              : "";
          zeichnen();
        });
        knoepfe.appendChild(vertrag);
        if ((a.links || []).length) {
          // Wunsch Ahmad 04.10.2026: "Vergleich öffnen" immer drücken können — wie "Vergleichen" im Programm
          const vergl = el("button", "knopf neben", "Vergleich öffnen");
          vergl.title = "Vergleichsseiten mit euren AutoSchnell-Einstellungen öffnen";
          vergl.disabled = !!z.oeffnetGerade;
          vergl.addEventListener("click", async (ev) => {
            if (!ev.isTrusted || z.oeffnetGerade) return;      // 2.6.2: Doppelklick oeffnete die Tabs doppelt
            z.oeffnetGerade = true;
            try {
              await vergleichOeffnen();
            } finally {
              z.oeffnetGerade = false;
              if (zustand === z) zeichnen();
            }
          });
          knoepfe.appendChild(vergl);
        }
        inhalt.appendChild(knoepfe);
      }
      box.appendChild(inhalt);
    }
    r.appendChild(box);
    box.scrollTop = scroll;
  }

  /** Installierte App ist zu: per Link-Typ web+autoschnell: starten (noch im Klick, sonst blockt der Browser). */
  async function appStarten(z, protokoll) {
    const a = document.createElement("a");
    a.href = protokoll;
    a.style.display = "none";
    (document.body || document.documentElement).appendChild(a);
    a.click();
    a.remove();
    z.protokollWartet = "";
    z.webseiteAnbieten = false;
    z.meldung = "AutoSchnell-App wird geöffnet …";
    zeichnen();
    const r3 = await A.senden({ typ: "app_start_pruefen", kennung: z.kennung });
    // 2.6.1: kein App-Fenster zu sehen -> fragen statt selbst die Webseite aufzumachen (die App kann in einem anderen
    // Browser aufgegangen sein, oder der Browser fragt noch "AutoSchnell öffnen?")
    const unklar = !r3 || r3.weg === "unklar";
    z.meldung = unklar ? "Hat sich die AutoSchnell-App geöffnet? Falls nicht (keine App installiert):" : "";
    z.webseiteAnbieten = unklar;
    zeichnen();
  }

  /** 2.6.1: "Webseite öffnen" — der Nutzer sagt, es gibt hier keine App; der Helfer merkt sich das. */
  async function webseiteStatt(z) {
    z.webseiteAnbieten = false;
    z.meldung = "AutoSchnell wird geöffnet …";
    zeichnen();
    const r = await A.senden({ typ: "vertrag", kennung: z.kennung, webseite: true });
    z.meldung = r && r.weg === "webseite" ? ""
      : (r && r.text) || "AutoSchnell konnte nicht geöffnet werden – Seite neu laden und noch einmal drücken.";
    zeichnen();
  }

  // ---------------------------------------------------------------- Inserat
  let aktuelleKennung = null;
  const FRISCH_MS = 15000;

  async function frischHolen(url) {
    // 2.6.0 (Nr. 5): mit Zeitgrenze — haengt das Portal, dreht "Inserat wird gelesen" nicht endlos
    const abbruch = new AbortController();
    const uhr = setTimeout(() => abbruch.abort(), FRISCH_MS);
    try {
      const r = await fetch(url, { credentials: "include", cache: "no-store", signal: abbruch.signal });
      return r.ok ? await r.text() : null;
    } catch (e) {
      return null;
    } finally {
      clearTimeout(uhr);
    }
  }

  /** 2.6.0 (Nr. 2): wie wurde die Seite geoeffnet? Der Hintergrund oeffnet Vergleiche nur von selbst, wenn man das
   *  Inserat wirklich ansieht — nicht im Hintergrund-Tab, nicht nach Neuladen/Zurueck, nicht bei verworfenen Tabs. */
  function ansicht(ersterAufruf) {
    let navTyp = "spa";                        // Wechsel ohne Neuladen ("naechstes Fahrzeug") = bewusst angeklickt
    if (ersterAufruf) {
      try {
        const n = performance.getEntriesByType("navigation")[0];
        navTyp = (n && n.type) || "navigate";
      } catch (e) {
        navTyp = "navigate";
      }
    }
    return { sichtbar: document.visibilityState === "visible", navTyp, verworfen: !!document.wasDiscarded };
  }

  async function inserat(kennung, ersterAufruf) {
    zustand = { kennung, phase: "laden" };
    zeichnen();
    // Tempo (2.4.0): erst fragen, ob der Helfer das Inserat gerade schon gelesen hat (Neuladen, zurueck, zweiter
    // Tab) — dann steht die Box sofort, ohne die Seite einzupacken oder neu zu holen.
    const wie = ansicht(ersterAufruf);
    let antwort = await A.senden({ typ: "inserat", kennung, url: location.href, ansicht: wie });
    if (!antwort || antwort.bekannt === false) {
      // Erster Aufruf: die geladene Seite enthaelt das Inserat. Nach einem Wechsel ohne Neuladen: frisch holen.
      let html = ersterAufruf ? document.documentElement.outerHTML : await frischHolen(location.href);
      if (!html) html = document.documentElement.outerHTML;
      antwort = await A.senden({ typ: "inserat", kennung, url: location.href, seite: await A.packen(html), ansicht: wie });
      if (antwort && antwort.fehler === "seite" && ersterAufruf) {
        const frisch = await frischHolen(location.href);
        if (frisch && kennung === aktuelleKennung) {
          antwort = await A.senden({ typ: "inserat", kennung, url: location.href, seite: await A.packen(frisch),
                                     ansicht: wie });
        }
      }
    }
    if (kennung !== aktuelleKennung) return;          // inzwischen weitergeklickt
    if (!antwort) {
      if (!A.helferDa()) { zustand = { kennung, phase: "laden" }; veraltet(); return; }
      zustand = { kennung, phase: "fehler", text: "Der AutoSchnell Helfer antwortet nicht – bitte erneut versuchen." };
    } else if (antwort.fehler) {
      zustand = { kennung, phase: "fehler", text: antwort.text || "Das Inserat konnte nicht gelesen werden.",
                  nichtVerbunden: antwort.fehler === "nicht_verbunden" };
    } else {
      // Eine schon eingetroffene Ampel (direkt geholte Vergleichsseite) nicht wieder wegwerfen
      const vorher = zustand && zustand.kennung === kennung ? zustand : {};
      zustand = { kennung, phase: "fertig", antwort: antwort.antwort, geoeffnet: antwort.geoeffnet,
                  schonOffen: antwort.schonOffen, ausVergleich: antwort.ausVergleich, vomProgramm: antwort.vomProgramm,
                  automatik: antwort.automatik || "", neueVersion: antwort.neueVersion || "",
                  marktlage: { ...(vorher.marktlage || {}), ...(antwort.marktlage || {}) },
                  lageFehler: { ...(antwort.lageFehler || {}), ...(vorher.lageFehler || {}) },
                  lageStart: Date.now() };
      // im Hintergrund geoeffnet und inzwischen doch sichtbar? Dann gleich oeffnen
      if (zustand.automatik === "hintergrund" && document.visibilityState === "visible") sichtbarGeworden();
    }
    zeichnen();
  }

  /** 2.6.0 (Nr. 2): Ein im Hintergrund geoeffnetes Inserat wird angesehen — jetzt erst von selbst vergleichen. */
  async function sichtbarGeworden() {
    if (document.visibilityState !== "visible" || !zustand || zustand.phase !== "fertig"
        || zustand.automatik !== "hintergrund" || !A.helferDa()) return;
    const kennung = zustand.kennung;
    zustand.automatik = "";                            // genau einmal
    const r = await A.senden({ typ: "vergleiche_auto", kennung });
    if (!zustand || zustand.kennung !== kennung) return;
    if (r && r.geoeffnet) {
      Object.assign(zustand, { geoeffnet: r.geoeffnet, lageFehler: {}, lageAbgelaufen: false, lageStart: Date.now() });
    } else {
      zustand.automatik = (r && r.automatik) || "";
    }
    zeichnen();
  }

  /** Erweiterung aktualisiert/neu geladen: die Box sagt es und bietet "Seite neu laden" (statt stummer Knoepfe). */
  function veraltet() {
    if (!zustand || zustand.veraltet) return;
    zustand.veraltet = true;
    zugeklappt = false;
    zeichnen();
  }

  /** Der Helfer kennt das Inserat nicht mehr (Browser neu gestartet, laenger als 2 h offen): die Seite noch einmal
   *  schicken — ohne von selbst Vergleiche zu oeffnen —, danach wiederholt der Knopf seine Aktion. */
  async function nachlesen(kennung) {
    const senden = async (html) => A.senden({ typ: "inserat", kennung, url: location.href, seite: await A.packen(html),
                                              ohneOeffnen: true });
    let r = await senden(document.documentElement.outerHTML);
    if (r && r.fehler === "seite") {                 // nach Seitenwechsel ohne Neuladen: frisch holen
      const frisch = await frischHolen(location.href);
      if (frisch) r = await senden(frisch);
    }
    return r;          // 2.6.0 (Nr. 5): mit Grund (nicht verbunden, kein Abo, zu viele …) statt nur ja/nein
  }

  /** "Vergleich öffnen" — aus der Box oder aus dem Fenster am AutoSchnell-Symbol (auch bei zugemachter Box). */
  async function vergleichOeffnen() {
    const kennung = aktuelleKennung;
    if (!kennung) return { fehler: "kein_inserat" };
    if (!A.helferDa()) { veraltet(); return { fehler: "veraltet" }; }
    const hier = () => zustand && zustand.kennung === kennung;
    if (hier()) { zustand.meldung = "Vergleiche werden geöffnet …"; zeichnen(); }
    let r = await A.senden({ typ: "vergleiche_oeffnen", kennung });
    if (r && r.fehler === "unbekannt") {
      const nach = await nachlesen(kennung);
      r = nach && nach.antwort ? await A.senden({ typ: "vergleiche_oeffnen", kennung }) : (nach || r);
    }
    if (!A.helferDa()) { veraltet(); return { fehler: "veraltet" }; }
    if (hier()) {
      if (r && r.geoeffnet) {
        // neue Vergleichsseiten: die Zeitgrenze der Ampel beginnt neu
        Object.assign(zustand, { geoeffnet: r.geoeffnet, automatik: "", lageFehler: {}, lageAbgelaufen: false,
                                 lageStart: Date.now() });
      }
      zustand.meldung = r && r.geoeffnet ? ""
        : r && !r.fehler ? "Für dieses Auto gibt es keinen Vergleich (Marke oder Modell unbekannt)."
          : (r && r.text) || "Die Vergleiche konnten nicht geöffnet werden – Seite neu laden und noch einmal drücken.";
      zeichnen();
    }
    return r || { fehler: "intern" };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg && msg.typ === "marktlage" && zustand && msg.kennung === zustand.kennung) {
      zustand.marktlage = { ...(zustand.marktlage || {}), [msg.portal]: msg.lage };
      zeichnen();
    }
    // 2.6.0: Vergleichsseite nicht auswertbar (vorlaeufig = nur der Direktabruf; der Tab kann es noch schaffen)
    if (msg && msg.typ === "marktlage_fehler" && zustand && msg.kennung === zustand.kennung) {
      const alt = (zustand.lageFehler || {})[msg.portal];
      if (!alt || alt.vorlaeufig) {
        zustand.lageFehler = { ...(zustand.lageFehler || {}), [msg.portal]: { text: msg.text, vorlaeufig: !!msg.vorlaeufig } };
        zeichnen();
      }
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
    let r = await A.senden({ typ: "suche", url: location.href, seite });
    // 2.6.2 (Pruefung 05.10.2026, Paket 1): kein Netz / Server kurz weg -> einmal von selbst wiederholen
    // (vorher galt der Tab danach fuer immer als erledigt — keine Ampel, auch nicht nach Neuladen)
    if (r && r.fehler === "vorlaeufig") {
      await new Promise((fertig) => setTimeout(fertig, 6000));
      r = await A.senden({ typ: "suche", url: location.href, seite });
    }
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

  // 2.6.0: zweimal installiert (zwei Erweiterungs-IDs, gemeinsam.js)? Dann nichts tun ausser es zu sagen — sonst
  // gehen Vergleiche doppelt auf und die beiden Boxen nehmen sich gegenseitig weg.
  if (A.andere && A.andere.size) {
    zustand = { phase: "doppelt" };
    zeichnen();
    return;
  }

  let letzte = location.href;
  const adresseGewechselt = () => {
    if (location.href !== letzte) {
      letzte = location.href;
      pruefen(false);
    }
  };

  function starten() {
    // 2.6.0 (Nr. 18): zugeklappt bleibt zugeklappt
    try {
      chrome.storage.local.get("boxZugeklappt").then((x) => {
        if (x && x.boxZugeklappt && !zugeklappt) { zugeklappt = true; if (zustand) zeichnen(); }
      }).catch(() => {});
    } catch (e) { /* egal */ }
    letzte = location.href;
    pruefen(true);
    // 2.6.0: Seitenwechsel ohne Neuladen sofort erkennen (Navigation API / zurueck); die Abfrage bleibt als Rueckfall
    try { if (window.navigation) window.navigation.addEventListener("currententrychange", adresseGewechselt); } catch (e) { /* alt */ }
    window.addEventListener("popstate", adresseGewechselt);
    // 2.6.0 (Nr. 2): im Hintergrund geoeffnete Inserate oeffnen ihre Vergleiche, wenn man hinwechselt
    document.addEventListener("visibilitychange", sichtbarGeworden);
    setInterval(() => {
      // Erweiterung inzwischen aktualisiert/neu geladen? Dann gleich sagen — nicht erst, wenn ein Knopf stumm bleibt
      if (zustand && !zustand.veraltet && !A.helferDa()) veraltet();
      // keine Ampel nach AMPEL_MS: einmal neu zeichnen, die Zeile sagt dann, was los ist
      if (zustand && zustand.phase === "fertig" && zustand.lageStart && !zustand.lageAbgelaufen
          && Date.now() - zustand.lageStart > AMPEL_MS) {
        zustand.lageAbgelaufen = true;
        zeichnen();
      }
      adresseGewechselt();
    }, 700);
  }

  // 2.6.0 (Pruefung 05.10.2026, Nr. 10): vorgeladene Seiten (Google-Treffer, Adresszeile) erst bearbeiten, wenn man
  // sie wirklich oeffnet — sonst wuerden Inserate hochgeladen und Vergleiche geoeffnet, die man nie ansieht
  if (document.prerendering) document.addEventListener("prerenderingchange", starten, { once: true });
  else starten();
})();
