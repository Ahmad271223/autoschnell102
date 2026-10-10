import { useEffect, useMemo, useRef, useState } from "react";
import { Camera, Trash2, Eraser, X } from "lucide-react";
import { toast } from "sonner";
import AbholFoto from "@/components/AbholFoto";
import { TECHNIK_BEREICHE, TECHNIK_TYP, fragenFuer, istTechnik, mitAntwort, mitBetrag, technikSchaden } from "@/lib/kiSchaden";

// Wunsch Ahmad 06.10.2026 (nur Fahrer-Protokoll): Lackdicke als EIGENE Markierung — kein Schaden, kommt
// nie in den Kaufvertrag. Punkt antippen, Wert in µm eintragen, Fotos möglich. Grenze wie der Server
// (routes/protocols.py LACKDICKE_MAX_UM).
export const LACK_TYP = { key: "lackdicke", abbr: "LD", label: "Lackdicke messen", color: "#94a3b8" };
export const LACKDICKE_MAX_UM = 5000;

/** Eingabe "180" / "1.250" -> Zahl in µm (null = leer oder unlesbar, gedeckelt auf den Serverwert). */
export function lackWert(text) {
  const ziffern = String(text ?? "").replace(/\D/g, "").slice(0, 4);
  if (!ziffern) return null;
  return Math.min(LACKDICKE_MAX_UM, Number(ziffern));
}

// Rollenprüfung 22.09.2026 (RP-070/RP-169/RP-514): Ein Tipp auf einen Marker
// löschte den Schaden sofort — und der Marker liegt genau auf dem Bauteilpunkt
// (größer als der Punkt). Wer am selben Bauteil einen ZWEITEN Schaden
// ("Delle" zusätzlich zu "Kratzer") setzen wollte, löschte still den ersten;
// "Alle entfernen" fragte gar nicht. Jetzt:
//  * Tipp auf einen Marker mit ANDERER aktiver Schadensart -> weiterer Schaden
//    am selben Bauteil (Marker wird daneben gezeichnet);
//  * gleiche Schadensart -> Rückfrage, dann entfernen;
//  * jedes Entfernen (Marker, Liste, "Alle entfernen") mit "Rückgängig".
const RUECKGAENGIG_MS = 8000;

/** Anzeige-Versatz für Marker, die am selben Punkt liegen (Index je Punkt). */
export function markerVersatz(markers) {
  const zaehler = new Map();
  return markers.map((m) => {
    const k = `${m.x}|${m.y}`;
    const i = zaehler.get(k) || 0;
    zaehler.set(k, i + 1);
    return i;
  });
}

/**
 * Schaden-Selector mit fixen Klick-Punkten je Fahrzeug-Ansicht.
 *
 * Workflow:
 *   1) Schadensart oben wählen.
 *   2) In einer der 5 Skizzen auf einen der vordefinierten Punkte klicken.
 *      Die Punkte sind klein und unauffällig im Bild eingezeichnet; das
 *      Label (z.B. "Motorhaube", "Linker Hauptscheinwerfer") ist im Bild
 *      unsichtbar und erscheint nur im Tooltip beim Hover.
 *   3) Marker mit Kürzel wird gesetzt; der zugehörige Karosserieteil-Name
 *      landet im Vertrag als lesbarer Text.
 *
 * Konvention (Deutschland, Linkslenker):
 *   - "links"  = Fahrerseite  (Auto schaut im Bild nach LINKS)
 *   - "rechts" = Beifahrerseite (Auto schaut im Bild nach RECHTS)
 *   Front-Ansicht (Auto zeigt zum Betrachter):
 *     "links"  (Fahrerseite)    = RECHTE Bildhälfte
 *     "rechts" (Beifahrerseite) = LINKE  Bildhälfte
 */

export const DAMAGE_TYPES = [
  { key: "unfall_repariert",       abbr: "UR", label: "Unfallschaden repariert",       color: "#10b981" },
  { key: "unfall_nicht_repariert", abbr: "UN", label: "Unfallschaden NICHT repariert", color: "#ef4444" },
  { key: "hagelschaden",           abbr: "HS", label: "Hagelschaden",                  color: "#ec4899" },
  { key: "steinschlag",            abbr: "SS", label: "Steinschlag",                   color: "#a855f7" },
  { key: "delle",                  abbr: "DE", label: "Delle",                         color: "#eab308" },
  { key: "kratzer",                abbr: "KR", label: "Kratzer",                       color: "#0ea5e9" },
  { key: "rost",                   abbr: "RO", label: "Rost",                          color: "#a16207" },
  { key: "beleuchtung",            abbr: "BL", label: "Beleuchtung defekt",            color: "#f59e0b" },
];

const VIEW_LABELS = {
  front: "Frontansicht",
  rear:  "Heckansicht",
  left:  "Fahrerseite (links)",
  right: "Beifahrerseite (rechts)",
  top:   "Draufsicht",
};

// Koordinatenraum der Skizzen: 1536 × 1024 (alle Punkte/Markierungen
// rechnen darin). Angezeigt wird seit 10.09.2026 eine kleine JPEG-Fassung
// (1024 px, ~55 KB statt ~400 KB PNG) — Befund Ahmad: die Skizzen luden
// am Handy beim Vertrag erstellen oft nicht. Neue Dateinamen (-v2), weil
// der Server /damage/* ein Jahr lang cachen darf.
const IMG_W = 1536;
const IMG_H = 1024;
const VIEW_IMAGES = {
  front: { src: "/damage/front-v2.jpg", w: IMG_W, h: IMG_H },
  rear:  { src: "/damage/rear-v2.jpg",  w: IMG_W, h: IMG_H },
  left:  { src: "/damage/left-v2.jpg",  w: IMG_W, h: IMG_H },
  right: { src: "/damage/right-v2.jpg", w: IMG_W, h: IMG_H },
  top:   { src: "/damage/top-v2.jpg",   w: IMG_W, h: IMG_H },
};

/** Skizze mit Lade- und Fehlerbehandlung: bis zu drei Versuche, danach ein
 *  Knopf zum Neuladen — eine leere Fläche ohne Erklärung gibt es nicht mehr. */
function SkizzenBild({ src, alt }) {
  const [versuch, setVersuch] = useState(0);
  const [zustand, setZustand] = useState("laedt"); // laedt | ok | fehler
  const url = versuch ? `${src}?r=${versuch}` : src;
  return (
    <>
      <img src={url} alt={alt} decoding="async" draggable={false}
           onLoad={() => setZustand("ok")}
           onError={() => {
             if (versuch < 3) { setVersuch((v) => v + 1); setZustand("laedt"); }
             else setZustand("fehler");
           }}
           className="absolute inset-0 w-full h-full select-none"
           style={{ objectFit: "fill", pointerEvents: "none",
                    opacity: zustand === "ok" ? 1 : 0, transition: "opacity .15s" }} />
      {zustand !== "ok" && (
        <div className="absolute inset-0 flex items-center justify-center text-[11px] text-zinc-500 bg-white"
             data-testid="skizze-status">
          {zustand === "laedt" ? "Skizze wird geladen…" : (
            <button type="button" className="underline text-zinc-700"
                    style={{ pointerEvents: "auto" }}
                    onClick={(e) => { e.stopPropagation(); setVersuch((v) => v + 1); setZustand("laedt"); }}>
              Skizze konnte nicht geladen werden – erneut versuchen
            </button>
          )}
        </div>
      )}
    </>
  );
}

// Hilfsfunktion: Punkt mit Mittelpunkt (Bild-Pixel) + Name.
const P = (name, cx, cy) => ({ name, cx, cy });

/* ------------------------------------------------------------------
   DOTS — fix definierte, klickbare Punkte je Ansicht.
   Im Bild visuell als kleine, unauffällige Kreise dargestellt.
   Das Label ist im Bild UNSICHTBAR und erscheint nur im Tooltip
   beim Hover sowie später als Text im Vertrag.
   ------------------------------------------------------------------ */
const DOTS = {
  /* ---------------- FRONTANSICHT (SUV, 1536x1024) ----------------
     Neu kalibriert direkt an public/damage/front.png (08/2026).
     Konvention: "rechts" (Beifahrerseite) = LINKE Bildhaelfte,
                 "links"  (Fahrerseite)    = RECHTE Bildhaelfte. */
  front: [
    P("Dach",                                765, 100),
    P("Windschutzscheibe",                   765, 225),
    P("A-Säule rechts",                      370, 240),
    P("A-Säule links",                      1160, 240),
    P("Rechter Außenspiegel",                265, 330),
    P("Linker Außenspiegel",                1270, 330),
    P("Motorhaube",                          765, 395),
    P("Marken-Emblem",                       765, 470),
    P("Rechter Hauptscheinwerfer",           410, 460),
    P("Linker Hauptscheinwerfer",           1120, 460),
    P("Kotflügel vorne rechts",              295, 530),
    P("Kotflügel vorne links",              1235, 530),
    P("Lufteinlass rechts",                  420, 610),
    P("Kühlergrill",                         765, 615),
    P("Lufteinlass links",                  1110, 610),
    P("Rechter Nebelscheinwerfer",           370, 712),
    P("Linker Nebelscheinwerfer",           1160, 712),
    P("Kennzeichenhalterung",                765, 715),
    P("Stoßstange vorne",                    765, 800),
    P("Rechtes Vorderrad / Reifen",          345, 890),
    P("Linkes Vorderrad / Reifen",          1190, 890),
  ],

  /* ---------------- HECKANSICHT (kalibriert an rear.png) ----------------
     Blick von hinten: "links" (Fahrerseite) = LINKE Bildhaelfte. */
  rear: [
    P("Dach",                       765, 145),
    P("Heckscheibe",                765, 255),
    P("Linker Außenspiegel",        310, 330),
    P("Rechter Außenspiegel",      1220, 330),
    P("Heckklappe",                 765, 350),
    P("Linkes Rücklicht",           445, 400),
    P("Rechtes Rücklicht",         1090, 400),
    P("Kennzeichen hinten",         765, 445),
    P("Kotflügel hinten links",     330, 520),
    P("Kotflügel hinten rechts",   1200, 520),
    P("Stoßstange hinten",          765, 600),
    P("Auspuff links",              475, 670),
    P("Auspuff rechts",            1060, 670),
    P("Linkes Hinterrad / Felge",   375, 785),
    P("Rechtes Hinterrad / Felge", 1160, 785),
  ],

  /* -------- FAHRERSEITE (Auto schaut nach LINKS, Front am LINKEN Bildrand,
     kalibriert an left.png) ------- */
  left: [
    P("Stoßstange vorne",            80, 585),
    P("Linker Hauptscheinwerfer",   155, 490),
    P("Kotflügel vorne links",      245, 495),
    P("Motorhaube",                 335, 430),
    P("A-Säule links",              525, 372),
    P("Windschutzscheibe",          615, 352),
    P("Linker Außenspiegel",        600, 418),
    P("Dach",                       850, 280),
    P("Tür vorne links",            720, 525),
    P("B-Säule links",              858, 370),
    P("Tür hinten links",           975, 520),
    P("Seitenscheibe hinten links",1180, 338),
    P("C-Säule links",             1295, 360),
    P("Kotflügel hinten links",    1340, 505),
    P("Heckklappe",                1450, 400),
    P("Linkes Rücklicht",          1435, 460),
    P("Stoßstange hinten",         1480, 560),
    P("Schweller links",            800, 650),
    P("Vorderrad / Felge links",    330, 620),
    P("Hinterrad / Felge links",   1205, 620),
  ],

  /* ------- BEIFAHRERSEITE (Auto schaut nach RECHTS, Front am RECHTEN Bildrand,
     kalibriert an right.png) ------- */
  right: [
    P("Stoßstange vorne",          1460, 585),
    P("Rechter Hauptscheinwerfer", 1355, 480),
    P("Kotflügel vorne rechts",    1255, 495),
    P("Motorhaube",                1190, 420),
    P("A-Säule rechts",            1040, 365),
    P("Windschutzscheibe",          945, 352),
    P("Rechter Außenspiegel",       905, 410),
    P("Dach",                       680, 280),
    P("Tür vorne rechts",           790, 520),
    P("B-Säule rechts",             658, 370),
    P("Tür hinten rechts",          540, 515),
    P("Seitenscheibe hinten rechts",350, 338),
    P("C-Säule rechts",             255, 360),
    P("Kotflügel hinten rechts",    215, 505),
    P("Heckklappe",                 105, 390),
    P("Rechtes Rücklicht",          110, 455),
    P("Stoßstange hinten",           75, 560),
    P("Schweller rechts",           740, 650),
    P("Vorderrad / Felge rechts",  1155, 610),
    P("Hinterrad / Felge rechts",   320, 610),
  ],

  /* -------------- DRAUFSICHT (Front am RECHTEN Bildrand, kalibriert an
     top.png — das Fahrzeug liegt im Bild NICHT mittig: Mittellinie ~y 470,
     Karosserie ca. y 195–745) --------------
     Linkslenker-Konvention beim Blick von oben mit Front rechts:
       "links"  (Fahrerseite)    = OBERE Bildhaelfte
       "rechts" (Beifahrerseite) = UNTERE Bildhaelfte  */
  top: [
    P("Stoßstange vorne",          1465, 490),
    P("Linker Hauptscheinwerfer",  1330, 270),
    P("Rechter Hauptscheinwerfer", 1330, 665),
    P("Kotflügel vorne links",     1240, 230),
    P("Kotflügel vorne rechts",    1240, 705),
    P("Motorhaube",                1235, 490),
    P("Windschutzscheibe",          960, 490),
    P("Linker Außenspiegel",        935, 185),
    P("Rechter Außenspiegel",       935, 735),
    P("A-Säule links",              900, 265),
    P("A-Säule rechts",             900, 680),
    P("Tür vorne links",            700, 245),
    P("Tür vorne rechts",           700, 705),
    P("Dach",                       600, 480),
    P("Tür hinten links",           500, 245),
    P("Tür hinten rechts",          500, 705),
    P("C-Säule links",              330, 260),
    P("C-Säule rechts",             330, 685),
    P("Kotflügel hinten links",     215, 235),
    P("Kotflügel hinten rechts",    215, 695),
    P("Heckscheibe",                210, 480),
    P("Heckklappe",                 135, 480),
    P("Linkes Rücklicht",           120, 320),
    P("Rechtes Rücklicht",          125, 640),
    P("Stoßstange hinten",           65, 480),
  ],
};

// Klick-Toleranz: wenn der User danebentippt, schnappen wir zum
// nächsten Dot innerhalb dieser Distanz (Image-Pixel).
const SNAP_RADIUS = 110;

function findNearestDot(view, x, y) {
  const dots = DOTS[view] || [];
  let best = null;
  let bestDist = Infinity;
  for (const d of dots) {
    const dx = x - d.cx;
    const dy = y - d.cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < bestDist) {
      bestDist = dist;
      best = d;
    }
  }
  if (best && bestDist <= SNAP_RADIUS) return best;
  return null;
}

/**
 * Fotos zu einer Markierung (Wunsch Ahmad 06.10.2026, nur Fahrer-Protokoll): Vorschaubilder, Entfernen und
 * ein Knopf "Foto" (Kamera oder Galerie). Hochgeladen wird über `onHinzu(schadenId, datei)` des Protokolls.
 */
function FotoLeiste({ schadenId, foto }) {
  const [laedt, setLaedt] = useState(false);
  if (!foto) return null;
  const eigene = foto.liste.filter((x) => x.schaden_id === schadenId);
  const voll = foto.belegt >= foto.max;
  const waehlen = async (e) => {
    const dateien = Array.from(e.target.files || []);
    e.target.value = "";
    if (!dateien.length) return;
    setLaedt(true);
    try {
      for (const datei of dateien) {
        // nacheinander: der Server zählt die Grenze mit
        const ok = await foto.onHinzu(schadenId, datei);
        if (ok === false) break;
      }
    } finally {
      setLaedt(false);
    }
  };
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5" data-testid={`damage-fotos-${schadenId}`}>
      {eigene.map((x) => (
        <span key={x.id} className="relative inline-block">
          <AbholFoto pfad={foto.pfad(x.id)} client={foto.client} size={48} label="Schadenfoto" />
          {foto.onWeg && (
            <button type="button" onClick={() => foto.onWeg(x.id)} aria-label="Foto entfernen"
                    data-testid={`damage-foto-weg-${x.id}`}
                    className="absolute -top-2 -right-2 w-6 h-6 rounded-full flex items-center justify-center text-white"
                    style={{ background: "var(--accent-red, #ef4444)" }}>
              <X size={12} />
            </button>
          )}
        </span>
      ))}
      {foto.onHinzu && (
        <label className={`inline-flex items-center gap-1 rounded-full border px-3 py-1.5 min-h-[36px] text-[11px] ${
                 voll || laedt ? "opacity-50" : "cursor-pointer text-zinc-300 hover:text-white"}`}
               style={{ borderColor: "var(--border-default)" }}
               title={voll ? `Höchstens ${foto.max} Fotos je Protokoll` : "Foto aufnehmen oder auswählen"}
               data-testid={`damage-foto-hinzu-${schadenId}`}>
          <Camera size={13} /> {laedt ? "Wird hochgeladen…" : "Foto"}
          <input type="file" accept="image/*" multiple className="hidden" disabled={voll || laedt}
                 onChange={waehlen} data-testid={`damage-foto-input-${schadenId}`} />
        </label>
      )}
    </div>
  );
}

/**
 * `lackMessungen` + `onLackChange` (nur Fahrer-Protokoll): zusätzliche Markierung „Lackdicke“.
 * `fotos` = { liste, max, onHinzu, onWeg, pfad, client } (nur Fahrer-Protokoll): Fotos je Markierung.
 * Ohne diese Angaben (Kaufvertrag) bleibt alles wie bisher.
 */
export default function DamageSelector({ damages = [], onChange, lackMessungen, onLackChange, fotos }) {
  const [activeType, setActiveType] = useState(DAMAGE_TYPES[5]); // default: Kratzer
  const mitLack = typeof onLackChange === "function";
  const lack = useMemo(() => (mitLack && Array.isArray(lackMessungen) ? lackMessungen : []),
    [mitLack, lackMessungen]);
  // Fotos zählen nur zu Markierungen, die es noch gibt (wie der Server)
  const markierungsIds = useMemo(() => new Set([...damages, ...lack].map((d) => d.id)), [damages, lack]);
  const foto = fotos ? {
    ...fotos,
    liste: (fotos.liste || []).filter((x) => markierungsIds.has(x.schaden_id)),
    belegt: (fotos.liste || []).filter((x) => markierungsIds.has(x.schaden_id)).length,
  } : null;

  const aktuellLack = useRef(lack);
  useEffect(() => { aktuellLack.current = lack; });
  const removeLack = (id) => {
    const weg = lack.filter((m) => m.id === id);
    onLackChange?.(lack.filter((m) => m.id !== id));
    if (weg.length) {
      toast.success(`Entfernt: Lackdicke – ${weg[0].zone}`, {
        duration: RUECKGAENGIG_MS,
        action: { label: "Rückgängig", onClick: () => {
          const da = new Set((aktuellLack.current || []).map((m) => m.id));
          onLackChange?.([...(aktuellLack.current || []), ...weg.filter((m) => !da.has(m.id))]);
        } },
      });
    }
  };
  const setLackWert = (id, text) => onLackChange?.(lack.map((m) => (m.id === id ? { ...m, wert_um: lackWert(text) } : m)));

  const handleDotClick = (view, dot) => {
    if (!activeType) return;
    if (activeType.key === LACK_TYP.key) {
      const messung = { id: `l-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, view,
                        zone: dot.name, x: dot.cx, y: dot.cy, wert_um: null };
      onLackChange?.([...lack, messung]);
      toast.success(`Lackdicke: ${dot.name} – bitte den Wert in µm eintragen`, { duration: 2200 });
      return;
    }
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const newDamage = {
      id,
      view,
      type_key: activeType.key,
      type_label: activeType.label,
      abbr: activeType.abbr,
      color: activeType.color,
      zone: dot.name,
      x: dot.cx,
      y: dot.cy,
    };
    const next = [...damages, newDamage];
    onChange?.(next, damagesToText(next));
    // Feedback vor allem für Touch-Geräte (dort gibt es keinen Hover-Tooltip):
    // kurz anzeigen, welches Bauteil getroffen wurde.
    toast.success(`${activeType.label}: ${dot.name}`, { duration: 1600 });
  };

  const handleSvgClick = (view, e) => {
    // Sicherheitsnetz: Wenn der User danebentippt, schnappen wir zum
    // nächsten Dot innerhalb von SNAP_RADIUS — sonst wird der Klick
    // ignoriert (freies Setzen ist deaktiviert).
    if (!activeType) return;
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dim = VIEW_IMAGES[view];
    const x = Math.round(((e.clientX - rect.left) / rect.width) * dim.w);
    const y = Math.round(((e.clientY - rect.top) / rect.height) * dim.h);
    const dot = findNearestDot(view, x, y);
    if (dot) handleDotClick(view, dot);
  };

  // Immer die AKTUELLE Liste — "Rückgängig" kann Sekunden später kommen, dann
  // hat der Nutzer vielleicht schon weitere Schäden gesetzt.
  const aktuell = useRef(damages);
  useEffect(() => { aktuell.current = damages; });

  const wiederherstellen = (entfernt) => {
    const da = new Set((aktuell.current || []).map((d) => d.id));
    const next = [...(aktuell.current || []), ...entfernt.filter((d) => !da.has(d.id))];
    onChange?.(next, damagesToText(next));
  };

  const mitRueckgaengig = (text, entfernt) => {
    toast.success(text, {
      duration: RUECKGAENGIG_MS,
      action: { label: "Rückgängig", onClick: () => wiederherstellen(entfernt) },
    });
  };

  const removeDamage = (id) => {
    const weg = damages.filter((d) => d.id === id);
    const next = damages.filter((d) => d.id !== id);
    onChange?.(next, damagesToText(next));
    if (weg.length) mitRueckgaengig(`Entfernt: ${weg[0].type_label} – ${weg[0].zone}`, weg);
  };

  // RP-514: Tipp auf einen vorhandenen Marker.
  const handleMarkerTap = (view, m) => {
    if (activeType && activeType.key !== m.type_key) {
      // Andere Schadensart gewählt -> zusätzlicher Schaden am selben Bauteil
      // (bzw. eine Lackdicke-Messung, wenn "Lackdicke" gewählt ist).
      handleDotClick(view, { name: m.zone, cx: m.x, cy: m.y });
      return;
    }
    if (!window.confirm(`„${m.type_label} – ${m.zone}“ entfernen?`)) return;
    if (m.type_key === LACK_TYP.key) removeLack(m.id);
    else removeDamage(m.id);
  };

  const clearAll = () => {
    const vorher = damages;
    if (!vorher.length) return;
    // Rollenprüfung 22.09.2026 (Review): Einzahl bei genau einem Schaden
    // (vorher "Alle 1 erfassten Schäden entfernen?" / "1 Schäden entfernt").
    const einer = vorher.length === 1;
    if (!window.confirm(einer ? "Den erfassten Schaden entfernen?"
      : `Alle ${vorher.length} erfassten Schäden entfernen?`)) return;
    onChange?.([], "");
    mitRueckgaengig(einer ? "1 Schaden entfernt" : `${vorher.length} Schäden entfernt`, vorher);
  };

  const grouped = useMemo(() => {
    const map = {};
    for (const d of damages) (map[d.view] ||= []).push(d);
    // Lackdicke-Messungen als eigene Marker (LD) auf derselben Skizze
    for (const m of lack) {
      (map[m.view] ||= []).push({ ...m, type_key: LACK_TYP.key, abbr: LACK_TYP.abbr, color: LACK_TYP.color,
                                  type_label: m.wert_um != null ? `Lackdicke ${m.wert_um} µm` : "Lackdicke" });
    }
    return map;
  }, [damages, lack]);

  // Technischer Mangel (25.09.2026 abends): kein Skizzenpunkt — Bereich
  // tippen, dann Stand/Fahrbereit/Warnleuchte und eine kurze Beschreibung.
  const [technikOffen, setTechnikOffen] = useState(false);
  const addTechnik = (bereich) => {
    const next = [...damages, technikSchaden(bereich)];
    onChange?.(next, damagesToText(next));
    setTechnikOffen(false);
    toast.success(`${TECHNIK_TYP.label}: ${bereich}`, { duration: 1600 });
  };
  const setNote = (id, note) => {
    const next = damages.map((x) => (x.id === id ? { ...x, note } : x));
    onChange?.(next, damagesToText(next));
  };

  // Alle fuenf Skizzen sofort vorladen, sobald der Dialog offen ist — dann
  // liegen sie beim Wechsel der Ansicht schon im Browser-Cache.
  useEffect(() => {
    Object.values(VIEW_IMAGES).forEach((v) => { const i = new Image(); i.src = v.src; });
  }, []);

  return (
    <div className="space-y-4">
      {/* Schadensart-Chips */}
      <div className="flex flex-wrap gap-2" data-testid="damage-types">
        {(mitLack ? [...DAMAGE_TYPES, LACK_TYP] : DAMAGE_TYPES).map((t) => {
          const active = activeType?.key === t.key;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setActiveType(t)}
              data-testid={`damage-type-${t.key}`}
              className={`flex items-center gap-2 rounded-full border px-3 py-1.5 tipp-40 text-xs transition ${
                active ? "text-white shadow-md" : "text-zinc-300 hover:text-white"
              }`}
              style={{
                borderColor: active ? t.color : "var(--border-default)",
                backgroundColor: active ? `${t.color}33` : "transparent",
              }}
            >
              <span
                className="inline-flex items-center justify-center rounded-md px-1.5 py-0.5 text-[10px] font-bold tracking-wide"
                style={{ backgroundColor: t.color, color: "#0a0a0a" }}
              >
                {t.abbr}
              </span>
              <span>{t.label}</span>
            </button>
          );
        })}
      </div>

      {/* Technische Maengel ohne Skizze */}
      <div className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--border-default)" }}
           data-testid="damage-technik">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs text-zinc-300">
            <span className="inline-flex items-center justify-center rounded-md px-1.5 py-0.5 text-[10px] font-bold tracking-wide"
                  style={{ backgroundColor: TECHNIK_TYP.color, color: "#0a0a0a" }}>{TECHNIK_TYP.abbr}</span>
            <span>{TECHNIK_TYP.label} (Motor, Getriebe, Elektrik, Klima …) – ohne Skizze</span>
          </div>
          <button type="button" onClick={() => setTechnikOffen((o) => !o)} data-testid="damage-technik-oeffnen"
                  aria-expanded={technikOffen}
                  className="rounded-full border px-3 py-1.5 tipp-40 text-xs text-zinc-300 hover:text-white"
                  style={{ borderColor: technikOffen ? TECHNIK_TYP.color : "var(--border-default)" }}>
            {technikOffen ? "Schließen" : "+ Mangel hinzufügen"}
          </button>
        </div>
        {technikOffen && (
          <div className="mt-2 flex flex-wrap gap-1.5" data-testid="damage-technik-bereiche">
            {TECHNIK_BEREICHE.map((b) => (
              <button key={b} type="button" onClick={() => addTechnik(b)} data-testid={`damage-technik-${b}`}
                      className="rounded-full px-2.5 py-1 text-[11px] min-h-[32px] border text-zinc-300 hover:text-white"
                      style={{ borderColor: "var(--border-default)" }}>
                {b}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between gap-3 text-[11px] text-zinc-500">
        <div>
          <span className="text-zinc-400">Anleitung:</span> Schadenstyp oben
          wählen → in einer der Skizzen auf einen der kleinen Punkte klicken.
          Hover zeigt den Namen, nach dem Klick wird der Eintrag automatisch
          in den Vertrag übernommen. Tipp auf einen gesetzten Marker: andere
          Schadensart = zusätzlich am selben Teil, gleiche = entfernen (mit Rückfrage).
        </div>
        {damages.length > 0 && (
          <button
            type="button"
            onClick={clearAll}
            className="inline-flex items-center gap-1 text-zinc-400 hover:text-red-400 shrink-0 min-h-[40px] px-1"
            data-testid="damage-clear-all"
          >
            <Eraser size={12} /> Alle entfernen
          </button>
        )}
      </div>

      {/* 5 Ansichten gleichzeitig — responsive Grid */}
      <div
        className="grid grid-cols-1 md:grid-cols-2 gap-3"
        data-testid="damage-grid"
      >
        <ViewCard view="front" markers={grouped.front || []}
                  activeColor={activeType?.color}
                  onDotClick={handleDotClick}
                  onSvgClick={handleSvgClick}
                  onMarkerTap={handleMarkerTap} />
        <ViewCard view="rear"  markers={grouped.rear  || []}
                  activeColor={activeType?.color}
                  onDotClick={handleDotClick}
                  onSvgClick={handleSvgClick}
                  onMarkerTap={handleMarkerTap} />
        <ViewCard view="left"  markers={grouped.left  || []}
                  activeColor={activeType?.color}
                  onDotClick={handleDotClick}
                  onSvgClick={handleSvgClick}
                  onMarkerTap={handleMarkerTap} />
        <ViewCard view="right" markers={grouped.right || []}
                  activeColor={activeType?.color}
                  onDotClick={handleDotClick}
                  onSvgClick={handleSvgClick}
                  onMarkerTap={handleMarkerTap} />
        <ViewCard view="top"   markers={grouped.top   || []}
                  activeColor={activeType?.color}
                  onDotClick={handleDotClick}
                  onSvgClick={handleSvgClick}
                  onMarkerTap={handleMarkerTap} />
      </div>

      {/* Erfasste Schäden */}
      {damages.length > 0 ? (
        <div className="space-y-1.5" data-testid="damage-list">
          <div className="overline">Erfasste Schäden ({damages.length})</div>
          <ul className="divide-y rounded-lg border" style={{ borderColor: "var(--border-default)" }}>
            {damages.map((d) => (
              <li key={d.id} className="px-3 py-2 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className="inline-flex items-center justify-center rounded-md px-1.5 py-0.5 text-[10px] font-bold shrink-0"
                      style={{ backgroundColor: d.color, color: "#0a0a0a" }}
                    >
                      {d.abbr}
                    </span>
                    <span className="text-zinc-200 truncate">{d.type_label}</span>
                    <span className="text-zinc-500">·</span>
                    <span className="text-zinc-400 truncate">
                      {d.zone}
                      {VIEW_LABELS[d.view] ? <span className="text-zinc-600"> ({VIEW_LABELS[d.view]})</span> : null}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeDamage(d.id)}
                    className="text-zinc-500 hover:text-red-400 shrink-0 tipp flex items-center justify-center -my-2 -mr-2"
                    data-testid={`damage-remove-${d.id}`}
                    aria-label="Schaden entfernen"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                {/* Wunsch Ahmad 25.09.2026 (KI-Schadennachlass): zwei Sekunden
                    mehr je Schaden — Groesse, Lack, Laenge, Funktion — machen die
                    Kostenschaetzung erst brauchbar. Antworten liegen in
                    severity_data am Schaden. */}
                {fragenFuer(d).length > 0 && (
                  <div className="mt-1.5 space-y-1" data-testid={`damage-fragen-${d.id}`}>
                    {fragenFuer(d).map((f) => (
                      <div key={f.key} className="flex flex-wrap items-center gap-1">
                        <span className="text-[11px] text-zinc-500 mr-1 w-24 shrink-0">{f.label}</span>
                        {f.options.map((o) => {
                          const aktiv = (d.severity_data || {})[f.key] === o;
                          return (
                            <button key={o} type="button"
                                    onClick={() => onChange?.(damages.map((x) => (x.id === d.id ? mitAntwort(x, f.key, o) : x)),
                                                              damagesToText(damages))}
                                    aria-pressed={aktiv}
                                    data-testid={`damage-frage-${d.id}-${f.key}-${o}`}
                                    className="rounded-full px-2.5 py-1 text-[11px] min-h-[32px] border transition-colors"
                                    style={aktiv
                                      ? { background: "var(--accent-red)", color: "#fff", borderColor: "var(--accent-red)" }
                                      : { background: "transparent", color: "var(--text-secondary)", borderColor: "var(--border-default)" }}>
                              {o}
                            </button>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                )}
                {fragenFuer(d).filter((f) => f.betragBei && (d.severity_data || {})[f.key] === f.betragBei).map((f) => (
                  <div key={f.betragKey} className="mt-1.5 flex items-center gap-2">
                    <span className="text-[11px] text-zinc-500 w-24 shrink-0">Betrag (€)</span>
                    <input value={(d.severity_data || {})[f.betragKey] || ""} inputMode="numeric" maxLength={6}
                           data-testid={`damage-betrag-${d.id}-${f.betragKey}`} placeholder="z. B. 1200"
                           onChange={(e) => onChange?.(damages.map((x) => (x.id === d.id ? mitBetrag(x, f.betragKey, e.target.value) : x)),
                                                       damagesToText(damages))}
                           className="w-32 rounded-lg border bg-transparent px-2.5 py-1.5 text-[12px] text-zinc-200"
                           style={{ borderColor: "var(--border-default)" }} />
                  </div>
                ))}
                {istTechnik(d) && (
                  <input value={d.note || ""} maxLength={200} data-testid={`damage-note-${d.id}`}
                         placeholder="Was genau? z. B. Automatik ruckelt beim Kaltstart"
                         onChange={(e) => setNote(d.id, e.target.value)}
                         className="mt-1.5 w-full rounded-lg border bg-transparent px-2.5 py-1.5 text-[12px] text-zinc-200"
                         style={{ borderColor: "var(--border-default)" }} />
                )}
                <FotoLeiste schadenId={d.id} foto={foto} />
              </li>
            ))}
          </ul>
          {!mitLack && (
            <div className="text-[11px] text-zinc-500">
              Diese Liste wird automatisch als Abschnitt „Schäden / Beschädigungen"
              in den Vertrag übernommen.
            </div>
          )}
        </div>
      ) : (
        <div className="text-[11px] text-zinc-500">Noch keine Schäden erfasst.</div>
      )}

      {/* Lackdicke-Messungen (nur Fahrer-Protokoll) — kein Schaden, nicht im Kaufvertrag */}
      {mitLack && lack.length > 0 && (
        <div className="space-y-1.5" data-testid="lack-list">
          <div className="overline">Lackdicke gemessen ({lack.length})</div>
          <ul className="divide-y rounded-lg border" style={{ borderColor: "var(--border-default)" }}>
            {lack.map((m) => (
              <li key={m.id} className="px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="inline-flex items-center justify-center rounded-md px-1.5 py-0.5 text-[10px] font-bold shrink-0"
                          style={{ backgroundColor: LACK_TYP.color, color: "#0a0a0a" }}>{LACK_TYP.abbr}</span>
                    <span className="text-zinc-400 truncate">
                      {m.zone}
                      {VIEW_LABELS[m.view] ? <span className="text-zinc-600"> ({VIEW_LABELS[m.view]})</span> : null}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <input value={m.wert_um ?? ""} inputMode="numeric" maxLength={4} placeholder="z. B. 120"
                           aria-label={`Lackdicke ${m.zone} in µm`} data-testid={`lack-wert-${m.id}`}
                           onChange={(e) => setLackWert(m.id, e.target.value)}
                           className="w-24 rounded-lg border bg-transparent px-2.5 py-1.5 text-[12px] text-zinc-200"
                           style={{ borderColor: m.wert_um == null ? "var(--accent-red, #ef4444)" : "var(--border-default)" }} />
                    <span className="text-[11px] text-zinc-500">µm</span>
                    <button type="button" onClick={() => removeLack(m.id)} aria-label="Messung entfernen"
                            data-testid={`lack-remove-${m.id}`}
                            className="text-zinc-500 hover:text-red-400 tipp flex items-center justify-center">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                <FotoLeiste schadenId={m.id} foto={foto} />
              </li>
            ))}
          </ul>
          <div className="text-[11px] text-zinc-500">
            Lackdicke ist kein Schaden — sie kommt nicht in den Kaufvertrag, der Händler sieht sie bei der Freigabe.
          </div>
        </div>
      )}
      {foto && (
        <div className="text-[11px] text-zinc-500" data-testid="damage-fotos-zaehler">
          Fotos: {foto.belegt} von {foto.max} · der Händler sieht sie {foto.sichtTage} Tage lang.
        </div>
      )}
    </div>
  );
}

function ViewCard({ view, markers, activeColor, onDotClick, onSvgClick, onMarkerTap, className = "" }) {
  const dim = VIEW_IMAGES[view];
  const dots = DOTS[view] || [];
  const markerR = view === "top" ? 28 : 26;
  const markerFs = view === "top" ? 24 : 22;
  const versatz = markerVersatz(markers);
  // Klickbare Dot-Größe — bewusst klein, damit die Skizze ruhig bleibt.
  // Der Hover-Halo macht den Hit-Bereich grosszuegig.
  const dotR = 14;
  const dotHaloR = 32;

  return (
    <div
      className={`rounded-xl border bg-white p-2 ${className}`}
      style={{ borderColor: "var(--border-default)" }}
    >
      <div className="flex items-center justify-between px-1 pb-1">
        <div className="text-[11px] font-semibold text-zinc-700">
          {VIEW_LABELS[view]}
        </div>
        {markers.length > 0 && (
          <span className="inline-flex items-center justify-center rounded-full bg-zinc-200 px-2 text-[10px] font-semibold text-zinc-700">
            {markers.length}
          </span>
        )}
      </div>
      <div
        className="relative w-full overflow-hidden rounded-md bg-white"
        style={{ aspectRatio: `${dim.w} / ${dim.h}` }}
      >
        <SkizzenBild src={dim.src} alt={VIEW_LABELS[view]} />
        <svg
          id={`dmg-${view}`}
          viewBox={`0 0 ${dim.w} ${dim.h}`}
          preserveAspectRatio="none"
          width="100%"
          height="100%"
          className="select-none block absolute inset-0"
          onClick={(e) => onSvgClick(view, e)}
          data-testid={`damage-svg-${view}`}
          style={{ touchAction: "manipulation" }}
        >
          {/* Eine zentrale Hover-Regel je Ansicht (statt pro Dot) — der
              Punkt wechselt bei Hover zur aktiven Schadensfarbe. */}
          <style>{`
            #dmg-${view} .damage-dot:hover .damage-dot-inner {
              fill: ${activeColor || "#0ea5e9"};
              stroke: #0a0a0a;
              stroke-width: 3;
              opacity: 0.95;
            }
          `}</style>
          {/* Skizze liegt als <img> unter dem SVG (Lade-/Fehlerbehandlung);
              das SVG ist durchsichtig und traegt nur Punkte und Markierungen. */}
          <rect x="0" y="0" width={dim.w} height={dim.h} fill="transparent" />

          {/* Klickbare Dots — unauffällig, Label im title (Hover-Tooltip) */}
          {dots.map((d) => (
            <Dot
              key={d.name}
              dot={d}
              r={dotR}
              haloR={dotHaloR}
              onClick={(e) => {
                e.stopPropagation();
                onDotClick(view, d);
              }}
            />
          ))}

          {/* Bereits gesetzte Marker — RP-514: mehrere Schäden am selben
              Bauteil nebeneinander (gespeichert bleibt der Bauteilpunkt). */}
          {markers.map((m, i) => {
            const n = versatz[i];
            const x = Math.min(dim.w - markerR, Math.max(markerR, m.x + n * markerR * 1.7));
            return (
              <Marker
                key={m.id}
                d={m}
                x={x}
                r={markerR}
                fs={markerFs}
                onTap={(e) => {
                  e.stopPropagation();
                  onMarkerTap(view, m);
                }}
              />
            );
          })}
        </svg>
      </div>
    </div>
  );
}

/** Unauffälliger Klick-Punkt im Bild — Label nur als <title>-Tooltip,
 *  visuell ein kleiner halbtransparenter Kreis. Hover-Halo macht den
 *  Trefferbereich für die Maus großzügig. Die Hover-Farbe kommt aus der
 *  zentralen <style>-Regel in ViewCard (eine je Ansicht). */
function Dot({ dot, r, haloR, onClick }) {
  return (
    <g transform={`translate(${dot.cx}, ${dot.cy})`}
       style={{ cursor: "pointer" }}
       className="damage-dot"
       onClick={onClick}>
      {/* Unsichtbarer Hover-Halo — vergrößert den Hit-Bereich */}
      <circle r={haloR} fill="transparent" />
      {/* Sichtbarer Punkt */}
      <circle
        r={r}
        className="damage-dot-inner"
        fill="rgba(15,23,42,0.18)"
        stroke="rgba(15,23,42,0.55)"
        strokeWidth="2.5"
      />
      <title>{dot.name}</title>
    </g>
  );
}

function Marker({ d, x, r, fs, onTap }) {
  return (
    <g transform={`translate(${x ?? d.x}, ${d.y})`} style={{ cursor: "pointer" }} onClick={onTap}
       data-testid={`damage-marker-${d.id}`}>
      <circle r={r} fill={d.color} stroke="#0a0a0a" strokeWidth="3" opacity="0.95" />
      <text
        textAnchor="middle"
        dy={fs * 0.36}
        fontSize={fs}
        fontWeight="800"
        fill="#0a0a0a"
        style={{ pointerEvents: "none" }}
      >
        {d.abbr}
      </text>
      <title>
        {d.type_label} – {d.zone} (Tippen mit gleicher Schadensart: entfernen; mit anderer: hinzufügen)
      </title>
    </g>
  );
}

/* ---------------------------- Text-Formatting ---------------------------- */

export function damagesToText(damages) {
  if (!damages || damages.length === 0) return "";
  const byType = new Map();
  for (const d of damages) {
    if (!byType.has(d.type_label)) byType.set(d.type_label, []);
    // Technischer Mangel: Bereich plus Beschreibung, damit der Vertrag ihn benennt
    const note = istTechnik(d) && d.note ? ` (${String(d.note).trim()})` : "";
    byType.get(d.type_label).push(`${d.zone || ""}${note}`);
  }
  const lines = [];
  for (const [type, zones] of byType) {
    const seen = new Set();
    const uniq = zones.filter((z) => (seen.has(z) ? false : (seen.add(z), true)));
    lines.push(`• ${type}: ${uniq.join(", ")}`);
  }
  return lines.join("\n");
}
