/**
 * Firmenstempel mit Unterschrift (Wunsch Ahmad 02.10.2026, aus seiner Vorlage übernommen — nur Stempel und
 * Unterschrift, keine Rechnung). Sechs Designs werden auf eine Leinwand gezeichnet; die gezeichnete Unterschrift
 * wird zugeschnitten und über den Stempel gelegt. Das Ergebnis ist ein PNG, das als „Unterschrift des Chefs“
 * hinterlegt wird (Kasten „Käufer“ im Kaufvertrag, Kundenportal).
 *
 * Alles reine Leinwand-Logik ohne React; `mk`/`messer` lassen sich in Tests durch Attrappen ersetzen.
 */
const PI = Math.PI;
export const FAM_MONO = '"Courier New","Liberation Mono",monospace';
export const FAM_SANS = '"Helvetica Neue",Helvetica,Arial,"Liberation Sans",sans-serif';

export const BEISPIEL = {
  name: "Mustermann Autohandel", zusatz: "Inh. Max Mustermann", strasse: "Musterstraße 1",
  ort: "12345 Musterstadt", tel: "01234 567890", mail: "info@beispiel.de", ust: "DE123456789",
};

export function initialen(name) {
  const w = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!w.length) return "F";
  const s = w.length > 1 ? w[0].charAt(0) + w[1].charAt(0) : w[0].slice(0, 2);
  return s.toUpperCase();
}

/** Firmendaten getrimmt; sind alle leer, das Beispiel (damit die Vorschau nie leer ist). */
export function firmendaten(d) {
  const t = (k) => String((d && d[k]) || "").trim();
  const roh = { name: t("name"), zusatz: t("zusatz"), strasse: t("strasse"), ort: t("ort"), tel: t("tel"), mail: t("mail"), ust: t("ust") };
  return Object.values(roh).some(Boolean) ? roh : { ...BEISPIEL };
}

function zufall(seed) {
  let s = seed | 0;
  return () => {
    s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function hash(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

// ---------------------------------------------------------------- Leinwand-Grundlagen
let _mess = null;
function messKontext() {
  if (_mess) return _mess;
  const c = document.createElement("canvas");
  _mess = c.getContext("2d");
  if (!_mess) throw new Error("Leinwand ohne 2D-Kontext");
  return _mess;
}
export function mk(w, h) {
  const c = document.createElement("canvas");
  c.width = Math.round(w); c.height = Math.round(h);
  const x = c.getContext("2d");
  if (!x) throw new Error("Leinwand ohne 2D-Kontext");
  x.fillStyle = "#FFFFFF"; x.fillRect(0, 0, c.width, c.height);
  x.fillStyle = "#000000"; x.strokeStyle = "#000000";
  return c;
}
function setFont(x, size, bold, fam) { x.font = `${bold ? "700 " : "400 "}${size}px ${fam}`; }
function rr(x, X, Y, w, h, r, lw) {
  x.lineWidth = lw; x.beginPath();
  x.moveTo(X + r, Y); x.arcTo(X + w, Y, X + w, Y + h, r); x.arcTo(X + w, Y + h, X, Y + h, r);
  x.arcTo(X, Y + h, X, Y, r); x.arcTo(X, Y, X + w, Y, r); x.closePath(); x.stroke();
}
function linie(x, x1, y1, x2, y2, lw) { x.lineWidth = lw; x.beginPath(); x.moveTo(x1, y1); x.lineTo(x2, y2); x.stroke(); }
function kreis(x, cx, cy, r, lw) { x.lineWidth = lw; x.beginPath(); x.arc(cx, cy, r, 0, 2 * PI); x.stroke(); }
function stern(x, cx, cy, r) {
  x.beginPath();
  for (let i = 0; i < 10; i += 1) {
    const a = -PI / 2 + i * PI / 5, q = (i % 2) ? r * 0.42 : r;
    x.lineTo(cx + q * Math.cos(a), cy + q * Math.sin(a));
  }
  x.closePath(); x.fill();
}

// ---------------------------------------------------------------- Textzeilen
export function baueZeilen(d, gleich, nameSize, restSize) {
  const L = [];
  if (d.name) L.push({ t: d.name.toUpperCase(), bold: true, size: nameSize });
  if (d.zusatz) L.push({ t: d.zusatz, size: restSize });
  if (d.strasse) L.push({ t: d.strasse, size: restSize });
  if (d.ort) L.push({ t: d.ort, size: restSize });
  if (gleich) {
    if (d.tel) L.push({ t: `Tel. ${d.tel}`, size: restSize });
    if (d.mail) L.push({ t: d.mail, size: restSize });
  } else {
    const k = [];
    if (d.tel) k.push(`Tel. ${d.tel}`);
    if (d.mail) k.push(d.mail);
    if (k.length) L.push({ t: k.join("  |  "), size: restSize * 0.85 });
  }
  if (d.ust) L.push({ t: `St.-Nr./USt-IdNr. ${d.ust}`, size: gleich ? restSize : restSize * 0.85 });
  return L;
}

/** Breite eines Texts in Pixeln — Standard über eine Mess-Leinwand, in Tests ersetzbar. */
export function textBreite(text, size, bold, fam, mess = null) {
  if (mess) return mess(text, size, bold, fam);
  const x = messKontext();
  setFont(x, size, bold, fam);
  return x.measureText(text).width;
}

export function layoutZeilen(L, maxW, gleich, fam, basis, mess = null) {
  if (gleich) L.forEach((l) => { l.size = basis; });
  L.forEach((l) => {
    const w = textBreite(l.t, l.size, l.bold, fam, mess);
    if (w > maxW) l.size = Math.max(14, Math.floor(l.size * maxW / w));
  });
  if (gleich && L.length) {
    const m = Math.min(...L.map((l) => l.size));
    L.forEach((l) => { l.size = m; });
  }
  let h = 0;
  L.forEach((l) => { l.h = l.size * 1.32; h += l.h; });
  return h;
}
function zeichneZeilen(x, L, fam, align, xp, y0) {
  let y = y0;
  x.textAlign = align; x.textBaseline = "middle";
  L.forEach((l) => {
    setFont(x, l.size, l.bold, fam);
    x.fillText(l.t, xp, y + l.h / 2);
    y += l.h;
  });
}

// Text auf dem Kreisbogen
function bogenMass(x, chars, size, bold, fam) {
  setFont(x, size, bold, fam);
  const track = size * 0.1;
  const ws = chars.map((ch) => x.measureText(ch).width);
  const tot = ws.reduce((s, w) => s + w + track, 0) - track;
  return { ws, track, tot };
}
function bogenText(x, text, cx, cy, R, size, bold, fam, maxSpan, oben) {
  const chars = Array.from(text || "");
  if (!chars.length) return;
  let m = bogenMass(x, chars, size, bold, fam);
  let groesse = size;
  if (m.tot / R > maxSpan) { groesse = size * maxSpan / (m.tot / R); m = bogenMass(x, chars, groesse, bold, fam); }
  const span = m.tot / R;
  let cum = 0;
  x.textAlign = "center"; x.textBaseline = "middle";
  for (let i = 0; i < chars.length; i += 1) {
    let a, rot;
    if (oben) { a = -PI / 2 - span / 2 + (cum + m.ws[i] / 2) / R; rot = a + PI / 2; }
    else { a = PI / 2 + span / 2 - (cum + m.ws[i] / 2) / R; rot = a - PI / 2; }
    x.save(); x.translate(cx + R * Math.cos(a), cy + R * Math.sin(a)); x.rotate(rot);
    x.fillText(chars[i], 0, 0); x.restore();
    cum += m.ws[i] + m.track;
  }
}
function ringText(x, str, cx, cy, R, fam) {
  setFont(x, 36, true, fam);
  const w36 = Array.from(str).reduce((s, ch) => s + x.measureText(ch).width, 0) || 1;
  const n = Math.max(1, Math.round(2 * PI * R / w36));
  const voll = str.repeat(n);
  const size = 36 * (2 * PI * R) / (n * w36) * 0.94;
  setFont(x, size, true, fam);
  const chars = Array.from(voll);
  const ws = chars.map((ch) => x.measureText(ch).width);
  const tot = ws.reduce((s, w) => s + w, 0);
  const sp = (2 * PI * R - tot) / Math.max(1, chars.length);
  let cum = 0;
  x.textAlign = "center"; x.textBaseline = "middle";
  for (let i = 0; i < chars.length; i += 1) {
    const a = -PI / 2 + (cum + ws[i] / 2) / R;
    x.save(); x.translate(cx + R * Math.cos(a), cy + R * Math.sin(a)); x.rotate(a + PI / 2);
    x.fillText(chars[i], 0, 0); x.restore();
    cum += ws[i] + sp;
  }
}

// ---------------------------------------------------------------- Die sechs Designs
function vKasten(d, u) {
  const B = 900, pad = 50;
  const L = baueZeilen(d, u, 60, 34);
  const h = layoutZeilen(L, B - 2 * pad - 30, u, FAM_MONO, 38);
  const H = Math.round(h + 2 * pad + 20);
  const c = mk(B, H), x = c.getContext("2d");
  rr(x, 6, 6, B - 12, H - 12, 22, 8);
  rr(x, 18, 18, B - 36, H - 36, 14, 3);
  zeichneZeilen(x, L, FAM_MONO, "center", B / 2, (H - h) / 2);
  return c;
}
function vRund(d, u) {
  const S = 800, cx = S / 2, cy = S / 2, R = 330;
  const c = mk(S, S), x = c.getContext("2d");
  kreis(x, cx, cy, 388, 9);
  kreis(x, cx, cy, 366, 3);
  kreis(x, cx, cy, 292, 3);
  const oben = (d.name || "").toUpperCase();
  const unten = [d.strasse, d.ort].filter(Boolean).join("  ·  ").toUpperCase();
  bogenText(x, oben, cx, cy, R, u ? 52 : 60, true, FAM_SANS, 2.9, true);
  bogenText(x, unten, cx, cy, R, u ? 52 : 46, true, FAM_SANS, 2.9, false);
  stern(x, cx - R, cy, 15);
  stern(x, cx + R, cy, 15);
  const mitte = [];
  if (d.zusatz) mitte.push({ t: d.zusatz, size: 36 });
  if (d.tel) mitte.push({ t: `Tel. ${d.tel}`, size: 36 });
  if (d.mail) mitte.push({ t: d.mail, size: 36 });
  if (d.ust) mitte.push({ t: d.ust, size: 36 });
  if (mitte.length) {
    const h = layoutZeilen(mitte, 430, true, FAM_SANS, 36);
    zeichneZeilen(x, mitte, FAM_SANS, "center", cx, cy - h / 2);
  } else {
    stern(x, cx, cy, 80);
  }
  return c;
}
function vRing(d) {
  const S = 800, cx = S / 2, cy = S / 2;
  const c = mk(S, S), x = c.getContext("2d");
  kreis(x, cx, cy, 390, 5);
  kreis(x, cx, cy, 292, 2);
  const str = `${[d.name, d.strasse, d.ort].filter(Boolean).map((s) => s.toUpperCase()).join("  •  ")}  •  `;
  ringText(x, str, cx, cy, 341, FAM_SANS);
  const m = initialen(d.name);
  let size = 210;
  setFont(x, size, true, FAM_SANS);
  const w = x.measureText(m).width;
  if (w > 330) size = size * 330 / w;
  setFont(x, size, true, FAM_SANS);
  x.textAlign = "center"; x.textBaseline = "middle";
  x.fillText(m, cx, cy - 20);
  linie(x, cx - 70, cy + 92, cx + 70, cy + 92, 4);
  const klein = (d.zusatz || d.ort || "").toUpperCase();
  if (klein) {
    let ks = 30;
    setFont(x, ks, false, FAM_SANS);
    const kw = x.measureText(klein).width;
    if (kw > 380) ks = ks * 380 / kw;
    setFont(x, ks, false, FAM_SANS);
    x.fillText(klein, cx, cy + 132);
  }
  return c;
}
function vText(d) {
  const B = 900, pad = 40;
  const L = baueZeilen(d, true, 40, 40);
  const h = layoutZeilen(L, B - 2 * pad, true, FAM_MONO, 40);
  const H = Math.round(h + 2 * pad);
  const c = mk(B, H), x = c.getContext("2d");
  zeichneZeilen(x, L, FAM_MONO, "center", B / 2, pad);
  return c;
}
function vLinien(d, u) {
  const B = 1000, padX = 70;
  const L = baueZeilen(d, u, 64, 32);
  const h = layoutZeilen(L, B - 2 * padX, u, FAM_SANS, 38);
  const H = Math.round(h + 128);
  const c = mk(B, H), x = c.getContext("2d");
  linie(x, 20, 14, B - 20, 14, 10);
  linie(x, 20, 32, B - 20, 32, 3);
  linie(x, 20, H - 32, B - 20, H - 32, 3);
  linie(x, 20, H - 14, B - 20, H - 14, 10);
  zeichneZeilen(x, L, FAM_SANS, "center", B / 2, 64);
  return c;
}
function vKapsel(d, u) {
  const B = 1000, pad = 34, rc = 80;
  const xs = 6 + 16 + 2 * rc + 44;
  const L = baueZeilen(d, u, 54, 30);
  const h = layoutZeilen(L, B - 6 - 40 - xs, u, FAM_SANS, 34);
  const H = Math.round(Math.max(h + 2 * pad, 212));
  const c = mk(B, H), x = c.getContext("2d");
  rr(x, 6, 6, B - 12, H - 12, (H - 12) / 2, 5);
  const cx = 6 + 16 + rc, cy = H / 2;
  x.beginPath(); x.arc(cx, cy, rc, 0, 2 * PI); x.fill();
  const m = initialen(d.name);
  let size = 80;
  setFont(x, size, true, FAM_SANS);
  const w = x.measureText(m).width;
  if (w > rc * 1.5) size = size * rc * 1.5 / w;
  setFont(x, size, true, FAM_SANS);
  x.fillStyle = "#FFFFFF"; x.textAlign = "center"; x.textBaseline = "middle";
  x.fillText(m, cx, cy + size * 0.04);
  x.fillStyle = "#000000";
  zeichneZeilen(x, L, FAM_SANS, "left", xs, (H - h) / 2);
  return c;
}

export const VARIANTEN = [
  { id: "kasten", name: "Klassisch", fn: vKasten },
  { id: "rund", name: "Rund", fn: vRund },
  { id: "ring", name: "Ring modern", fn: vRing },
  { id: "text", name: "Nur Schrift", fn: vText },
  { id: "linien", name: "Linien", fn: vLinien },
  { id: "kapsel", name: "Kapsel", fn: vKapsel },
];
export const VARIANTE_STANDARD = "kasten";

// ---------------------------------------------------------------- Abnutzung, Unterschrift, Zusammensetzen
function abnutzen(c, seedText) {
  const x = c.getContext("2d");
  const bild = x.getImageData(0, 0, c.width, c.height), p = bild.data;
  const r = zufall(hash(seedText));
  for (let i = 0; i < p.length; i += 4) {
    if (p[i] < 140 && r() < 0.07) { p[i] = 255; p[i + 1] = 255; p[i + 2] = 255; }
  }
  x.putImageData(bild, 0, 0);
}

function zeichneUnterschrift(x, W, H, m, sig, einst) {
  if (!sig || !einst.an) return;
  let tw = W * (Number(einst.groesse) || 55) / 100, th = tw * sig.h / sig.w;
  if (th > H * 0.9) { th = H * 0.9; tw = th * sig.w / sig.h; }
  const cx = m + W * (Number(einst.x) || 50) / 100, cy = m + H * (Number(einst.y) || 65) / 100;
  x.save(); x.translate(cx, cy); x.rotate(-3 * PI / 180);
  x.drawImage(sig.img, -tw / 2, -th / 2, tw, th);
  x.restore();
}

/**
 * Den fertigen Stempel zeichnen.
 * @param {object} o  {daten, variante, gleich, schraeg, abnutzung, unterschrift: {img, w, h}|null, einst: {an, groesse, x, y}}
 * @returns {HTMLCanvasElement}
 */
export function stempelErzeugen(o) {
  const d = firmendaten(o.daten);
  const v = VARIANTEN.find((e) => e.id === o.variante) || VARIANTEN[0];
  const u = !!o.gleich;
  const basis = v.fn(d, u), W = basis.width, H = basis.height, m = 50;
  const c = mk(W + 2 * m, H + 2 * m), x = c.getContext("2d");
  x.save();
  x.translate(c.width / 2, c.height / 2);
  if (o.schraeg) x.rotate(-1.6 * PI / 180);
  x.drawImage(basis, -W / 2, -H / 2);
  x.restore();
  if (o.abnutzung) abnutzen(c, JSON.stringify(d));
  zeichneUnterschrift(x, W, H, m, o.unterschrift, o.einst || { an: true, groesse: 55, x: 50, y: 65 });
  return c;
}

/** Ist dieser Bildpunkt Tinte? (dunkel und sichtbar — weiss oder durchsichtig zaehlt nicht) */
export function istTinte(p, i) {
  return p[i + 3] > 20 && (p[i] + p[i + 1] + p[i + 2]) / 3 < 200;
}

/**
 * Die gezeichnete Unterschrift (PNG-Data-URL, auch mit weissem Hintergrund) auf die Tinte zuschneiden und den
 * Hintergrund durchsichtig machen, damit die Stempellinien darunter sichtbar bleiben. null = keine Tinte.
 * `laden(url)` liefert ein Bild (in Tests ersetzbar).
 */
export async function unterschriftZuschneiden(dataUrl, laden = bildLaden) {
  if (!dataUrl) return null;
  const img = await laden(dataUrl);
  const w = img.width, h = img.height;
  if (!w || !h) return null;
  const roh = mk(w, h), rx = roh.getContext("2d");
  rx.clearRect(0, 0, w, h);
  rx.drawImage(img, 0, 0, w, h);
  const bild = rx.getImageData(0, 0, w, h), p = bild.data;
  let minX = w, minY = h, maxX = -1, maxY = -1;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      if (istTinte(p, i)) {
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
      } else {
        p[i + 3] = 0;                                   // Hintergrund durchsichtig
      }
    }
  }
  if (maxX < 0) return null;
  rx.putImageData(bild, 0, 0);
  const rand = 8;
  minX = Math.max(0, minX - rand); minY = Math.max(0, minY - rand);
  maxX = Math.min(w - 1, maxX + rand); maxY = Math.min(h - 1, maxY + rand);
  const cw = maxX - minX + 1, ch = maxY - minY + 1;
  const cc = mk(cw, ch), cx = cc.getContext("2d");
  cx.clearRect(0, 0, cw, ch);
  cx.drawImage(roh, minX, minY, cw, ch, 0, 0, cw, ch);
  const url = cc.toDataURL("image/png");
  const fertig = await laden(url);
  return { img: fertig, w: cw, h: ch, dataUrl: url };
}

export function bildLaden(url) {
  return new Promise((ok, nein) => {
    const img = new Image();
    img.onload = () => ok(img);
    img.onerror = () => nein(new Error("Bild nicht lesbar"));
    img.src = url;
  });
}
