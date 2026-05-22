import { useMemo, useState } from "react";
import { Trash2, Eraser } from "lucide-react";

/**
 * Schaden-Selector mit 5 Fahrzeug-Ansichten und 8 Schadensarten.
 *
 * Workflow:
 *   1) Schadensart oben wählen.
 *   2) In einer der 5 Skizzen auf die betroffene Stelle klicken.
 *   3) Marker mit Kürzel wird gesetzt, das nächstgelegene Karosserieteil
 *      identifiziert und als Eintrag in die Liste übernommen.
 *
 * Layout: alle 5 Ansichten sind dauerhaft sichtbar (Grid), keine Tabs —
 * der Händler kann direkt zur passenden Ansicht klicken.
 *
 * Konvention (Deutschland, Linkslenker):
 *   - "links"  = Fahrerseite  (Auto schaut im Bild nach LINKS)
 *   - "rechts" = Beifahrerseite (Auto schaut im Bild nach RECHTS)
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

// Alle neuen Skizzen liegen einheitlich bei 1536 × 1024 px in
// /app/frontend/public/damage/. Die Klick-Koordinaten werden im
// Image-Pixel-Raum gespeichert, damit Marker auch im PDF identisch
// gerendert werden.
const IMG_W = 1536;
const IMG_H = 1024;
const VIEW_IMAGES = {
  front: { src: "/damage/front.png", w: IMG_W, h: IMG_H },
  rear:  { src: "/damage/rear.png",  w: IMG_W, h: IMG_H },
  left:  { src: "/damage/left.png",  w: IMG_W, h: IMG_H },
  right: { src: "/damage/right.png", w: IMG_W, h: IMG_H },
  top:   { src: "/damage/top.png",   w: IMG_W, h: IMG_H },
};

// Hilfsfunktion: Zone mit Mittelpunkt + bbox-Größe (in Bild-Pixel).
const Z = (name, cx, cy, w = 160, h = 160) => ({
  name, cx, cy, x: cx - w / 2, y: cy - h / 2, w, h,
});

// Zonen wurden auf Basis der tatsächlichen Bounding-Boxen der Skizzen
// kalibriert (Porsche-Cayenne-Look, 1536 × 1024).
//
// WICHTIG — KFZ-Konvention (Fahrerperspektive):
//   "links"  / "rechts" beziehen sich IMMER auf die Fahrer-Sichtweise
//   (sitzend im Wagen, Blick nach vorn — also Fahrerseite vs. Beifahrerseite).
//   Daraus folgt für die einzelnen Bilder:
//     • Frontansicht (Auto schaut den Betrachter an):
//         "links"  (Fahrerseite)    = RECHTE Bildhälfte
//         "rechts" (Beifahrerseite) = LINKE  Bildhälfte
//     • Heckansicht (Auto vom Betrachter abgewandt — gleiche Blickrichtung wie Fahrer):
//         "links"  = LINKE  Bildhälfte
//         "rechts" = RECHTE Bildhälfte
//     • Draufsicht mit Front rechts:
//         "links"  = OBERE Bildhälfte
//         "rechts" = UNTERE Bildhälfte
//     • Seitenansichten: nur eine Seite des Fahrzeugs sichtbar — entspricht
//       jeweils der Fahrer- bzw. Beifahrerseite.
const ZONES = {
  /* ---------------- FRONTANSICHT (Auto schaut zum Betrachter) ----------------
     Fahrerseite (links) = RECHTE Bildhälfte, Beifahrerseite (rechts) = LINKE Bildhälfte. */
  front: [
    Z("Dach",                       765, 170, 900, 140),
    Z("Windschutzscheibe",          765, 320, 800, 180),
    Z("Außenspiegel rechts",        290, 440, 200, 100),  // Bei­fahrerseite -> linke Bildseite
    Z("Außenspiegel links",        1240, 440, 200, 100),  // Fahrerseite     -> rechte Bildseite
    Z("Motorhaube",                 765, 510, 600, 140),
    Z("Frontscheinwerfer rechts",   490, 620, 220, 130),
    Z("Frontscheinwerfer links",   1040, 620, 220, 130),
    Z("Kotflügel vorne rechts",     290, 720, 240, 280),
    Z("Kotflügel vorne links",     1240, 720, 240, 280),
    Z("Kühlergrill",                765, 760, 460, 120),
    Z("Kennzeichen vorne",          765, 870, 220, 90),
    Z("Stoßstange vorne",           765, 880, 720, 160),
    Z("Felge vorne rechts",         380, 940, 280, 200),
    Z("Felge vorne links",         1150, 940, 280, 200),
  ],

  /* ---------------- HECKANSICHT (Auto vom Betrachter abgewandt) ----------------
     Gleiche Sichtrichtung wie der Fahrer: links/rechts = links/rechts im Bild.
     Kalibriert anhand der tatsächlichen Bildmerkmale (PIL-Slice-Analyse). */
  rear: [
    Z("Dach",                       765, 180, 900, 140),
    Z("Heckscheibe",                765, 320, 850, 260),
    Z("Außenspiegel links",         440, 220, 200, 120),
    Z("Außenspiegel rechts",       1090, 220, 200, 120),
    Z("Heckklappe",                 765, 560, 380, 180),
    Z("Rücklicht links",            440, 590, 280, 180),
    Z("Rücklicht rechts",          1100, 590, 280, 180),
    Z("Kennzeichen hinten",         765, 660, 240, 90),
    Z("Kotflügel hinten links",     310, 600, 220, 280),
    Z("Kotflügel hinten rechts",   1220, 600, 220, 280),
    Z("Stoßstange hinten",          765, 770, 720, 110),
    Z("Auspuff links",              620, 730, 220, 130),
    Z("Auspuff rechts",             920, 730, 220, 130),
    Z("Felge hinten links",         320, 870, 260, 200),
    Z("Felge hinten rechts",       1210, 870, 260, 200),
  ],

  /* -------- FAHRERSEITE (Auto schaut nach LINKS, Front am linken Bildrand) ------- */
  left: [
    // Front-Bereich (links im Bild)
    Z("Stoßstange vorne",            85, 540, 150, 160),
    Z("Frontscheinwerfer links",    140, 460, 140, 100),
    Z("Kotflügel vorne links",      230, 540, 240, 200),
    Z("Motorhaube",                 360, 410, 280, 130),
    Z("Außenspiegel links",         500, 410, 100, 100),
    Z("Windschutzscheibe",          580, 340, 280, 160),
    Z("A-Säule links",              530, 360, 60, 160),
    // Dach + Mitte
    Z("Dach",                       820, 290, 620, 110),
    Z("B-Säule links",              820, 380, 60, 180),
    Z("Tür vorne links",            720, 540, 300, 280),
    Z("Tür hinten links",          1020, 540, 300, 280),
    Z("C-Säule links",             1100, 380, 60, 160),
    // Heck-Bereich (rechts im Bild)
    Z("Heckscheibe",               1280, 340, 280, 160),
    Z("Kotflügel hinten links",    1330, 540, 240, 200),
    Z("Heckklappe",                1430, 460, 160, 240),
    Z("Rücklicht links",           1450, 500, 100, 100),
    Z("Stoßstange hinten",         1450, 580, 130, 140),
    // Schweller + Felgen
    Z("Schweller links",            720, 690, 900, 50),
    Z("Felge vorne links",          250, 630, 280, 200),
    Z("Felge hinten links",        1280, 630, 280, 200),
  ],

  /* ------- BEIFAHRERSEITE (Auto schaut nach RECHTS, Front am rechten Bildrand) ------- */
  right: [
    // Front-Bereich (rechts im Bild) — gespiegelt zur Fahrerseite
    Z("Stoßstange vorne",          1450, 540, 150, 160),
    Z("Frontscheinwerfer rechts",  1395, 460, 140, 100),
    Z("Kotflügel vorne rechts",    1305, 540, 240, 200),
    Z("Motorhaube",                1175, 410, 280, 130),
    Z("Außenspiegel rechts",       1035, 410, 100, 100),
    Z("Windschutzscheibe",          955, 340, 280, 160),
    Z("A-Säule rechts",            1005, 360, 60, 160),
    // Dach + Mitte
    Z("Dach",                       715, 290, 620, 110),
    Z("B-Säule rechts",             715, 380, 60, 180),
    Z("Tür vorne rechts",           815, 540, 300, 280),
    Z("Tür hinten rechts",          515, 540, 300, 280),
    Z("C-Säule rechts",             435, 380, 60, 160),
    // Heck-Bereich (links im Bild)
    Z("Heckscheibe",                255, 340, 280, 160),
    Z("Kotflügel hinten rechts",    205, 540, 240, 200),
    Z("Heckklappe",                 105, 460, 160, 240),
    Z("Rücklicht rechts",            85, 500, 100, 100),
    Z("Stoßstange hinten",           85, 580, 130, 140),
    // Schweller + Felgen
    Z("Schweller rechts",           815, 690, 900, 50),
    Z("Felge vorne rechts",        1285, 630, 280, 200),
    Z("Felge hinten rechts",        255, 630, 280, 200),
  ],

  /* -------------- DRAUFSICHT (Front rechts im Bild, Heck links) -------------- */
  /* Linkslenker-Konvention:
   *   - "links"  (Fahrerseite)    = OBERE Bildhälfte
   *   - "rechts" (Beifahrerseite) = UNTERE Bildhälfte
   */
  top: [
    // Front (rechte Bildhälfte)
    Z("Stoßstange vorne",          1430, 460, 140, 380),
    Z("Frontscheinwerfer links",   1340, 280, 160, 130),
    Z("Frontscheinwerfer rechts",  1340, 640, 160, 130),
    Z("Kotflügel vorne links",     1230, 230, 220, 140),
    Z("Kotflügel vorne rechts",    1230, 690, 220, 140),
    Z("Motorhaube",                1130, 460, 320, 380),
    // Mitte vorne (Windschutzscheibe + A-Säule + Spiegel)
    Z("Windschutzscheibe",          900, 460, 220, 380),
    Z("A-Säule links",              930, 270, 60, 80),
    Z("A-Säule rechts",             930, 650, 60, 80),
    Z("Außenspiegel links",         960, 200, 100, 90),
    Z("Außenspiegel rechts",        960, 720, 100, 90),
    // Türen
    Z("Tür vorne links",            780, 240, 280, 130),
    Z("Tür vorne rechts",           780, 680, 280, 130),
    // Dach
    Z("Dach",                       620, 460, 320, 360),
    // Hintere Türen
    Z("Tür hinten links",           480, 240, 280, 130),
    Z("Tür hinten rechts",          480, 680, 280, 130),
    Z("C-Säule links",              400, 270, 60, 80),
    Z("C-Säule rechts",             400, 650, 60, 80),
    // Heckscheibe + Heck (linke Bildhälfte)
    Z("Heckscheibe",                340, 460, 200, 380),
    Z("Kotflügel hinten links",     220, 230, 220, 140),
    Z("Kotflügel hinten rechts",    220, 690, 220, 140),
    Z("Heckklappe",                 180, 460, 240, 380),
    Z("Rücklicht links",            120, 280, 130, 130),
    Z("Rücklicht rechts",           120, 640, 130, 130),
    Z("Stoßstange hinten",          100, 460, 140, 380),
  ],
};

function findClosestZone(view, x, y) {
  const zones = ZONES[view] || [];
  let best = null;
  let bestDist = Infinity;
  for (const z of zones) {
    const dx = x - z.cx;
    const dy = y - z.cy;
    const inside = x >= z.x && x <= z.x + z.w && y >= z.y && y <= z.y + z.h;
    // Starke Präferenz für Klicks innerhalb der bbox.
    const d = Math.sqrt(dx * dx + dy * dy) - (inside ? 5000 : 0);
    if (d < bestDist) {
      bestDist = d;
      best = z;
    }
  }
  return best?.name || "Karosserie";
}

export default function DamageSelector({ damages = [], onChange }) {
  const [activeType, setActiveType] = useState(DAMAGE_TYPES[5]); // default: Kratzer

  const handleSvgClick = (view, e) => {
    if (!activeType) return;
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dim = VIEW_IMAGES[view];
    // Da die SVG via Wrapper-Div auf das exakte Aspect-Ratio gezwungen wird
    // und preserveAspectRatio="none" gesetzt ist, ist das Mapping linear:
    // SVG-Element belegt die gesamte Wrapper-Fläche, viewBox 0..dim.w/dim.h.
    const x = Math.round(((e.clientX - rect.left) / rect.width) * dim.w);
    const y = Math.round(((e.clientY - rect.top) / rect.height) * dim.h);
    if (x < 0 || x > dim.w || y < 0 || y > dim.h) return;
    const zone = findClosestZone(view, x, y);
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const newDamage = {
      id,
      view,
      type_key: activeType.key,
      type_label: activeType.label,
      abbr: activeType.abbr,
      color: activeType.color,
      zone,
      x,
      y,
    };
    const next = [...damages, newDamage];
    onChange?.(next, damagesToText(next));
  };

  const removeDamage = (id) => {
    const next = damages.filter((d) => d.id !== id);
    onChange?.(next, damagesToText(next));
  };

  const clearAll = () => onChange?.([], "");

  const grouped = useMemo(() => {
    const map = {};
    for (const d of damages) (map[d.view] ||= []).push(d);
    return map;
  }, [damages]);

  return (
    <div className="space-y-4">
      {/* Schadensart-Chips */}
      <div className="flex flex-wrap gap-2" data-testid="damage-types">
        {DAMAGE_TYPES.map((t) => {
          const active = activeType?.key === t.key;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setActiveType(t)}
              data-testid={`damage-type-${t.key}`}
              className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition ${
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

      <div className="flex items-center justify-between gap-3 text-[11px] text-zinc-500">
        <div>
          <span className="text-zinc-400">Anleitung:</span> Schadenstyp oben
          wählen → in einer der Skizzen auf die Stelle klicken. Marker mit
          Kürzel erscheint, Eintrag wird automatisch in den Vertrag übernommen.
        </div>
        {damages.length > 0 && (
          <button
            type="button"
            onClick={clearAll}
            className="inline-flex items-center gap-1 text-zinc-400 hover:text-red-400 shrink-0"
            data-testid="damage-clear-all"
          >
            <Eraser size={12} /> Alle entfernen
          </button>
        )}
      </div>

      {/* Alle 5 Ansichten gleichzeitig — responsive Grid.
          Layout (alle Karten gleich groß, jeweils ½ Breite auf md):
            md: 2 Spalten   (Front | Heck;  Fahrer | Beifahrer;  Draufsicht | leer)
            sm: 1 Spalte    (alles untereinander)
      */}
      <div
        className="grid grid-cols-1 md:grid-cols-2 gap-3"
        data-testid="damage-grid"
      >
        <ViewCard view="front" markers={grouped.front || []} onSvgClick={handleSvgClick} onMarkerRemove={removeDamage} />
        <ViewCard view="rear"  markers={grouped.rear  || []} onSvgClick={handleSvgClick} onMarkerRemove={removeDamage} />
        <ViewCard view="left"  markers={grouped.left  || []} onSvgClick={handleSvgClick} onMarkerRemove={removeDamage} />
        <ViewCard view="right" markers={grouped.right || []} onSvgClick={handleSvgClick} onMarkerRemove={removeDamage} />
        <ViewCard view="top"   markers={grouped.top   || []} onSvgClick={handleSvgClick} onMarkerRemove={removeDamage} />
      </div>

      {/* Erfasste Schäden */}
      {damages.length > 0 ? (
        <div className="space-y-1.5" data-testid="damage-list">
          <div className="overline">Erfasste Schäden ({damages.length})</div>
          <ul className="divide-y rounded-lg border" style={{ borderColor: "var(--border-default)" }}>
            {damages.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
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
                    {d.zone}{" "}
                    <span className="text-zinc-600">({VIEW_LABELS[d.view]})</span>
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => removeDamage(d.id)}
                  className="text-zinc-500 hover:text-red-400 shrink-0"
                  data-testid={`damage-remove-${d.id}`}
                  aria-label="Schaden entfernen"
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
          <div className="text-[11px] text-zinc-500">
            Diese Liste wird automatisch als Abschnitt „Schäden / Beschädigungen"
            in den Vertrag übernommen.
          </div>
        </div>
      ) : (
        <div className="text-[11px] text-zinc-500">Noch keine Schäden erfasst.</div>
      )}
    </div>
  );
}

function ViewCard({ view, markers, onSvgClick, onMarkerRemove, className = "" }) {
  const dim = VIEW_IMAGES[view];
  // Marker-Größe je nach Ansicht (Image-Pixel-Raum) — größer für die kleineren Detail-Ansichten
  const markerR = view === "top" ? 28 : 26;
  const markerFs = view === "top" ? 24 : 22;

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
      {/* Wrapper-Div erzwingt das exakte Aspect-Ratio, SVG füllt zu 100% — so
          ist das Mapping zwischen Klick-Position und Bild-Pixel linear und
          der SVG-Renderer weicht nicht auf eine quadratische Default-Höhe aus. */}
      <div
        className="relative w-full overflow-hidden rounded-md"
        style={{ aspectRatio: `${dim.w} / ${dim.h}` }}
      >
        <svg
          viewBox={`0 0 ${dim.w} ${dim.h}`}
          preserveAspectRatio="none"
          width="100%"
          height="100%"
          className="cursor-crosshair select-none block absolute inset-0"
          onClick={(e) => onSvgClick(view, e)}
          data-testid={`damage-svg-${view}`}
          style={{ touchAction: "manipulation" }}
        >
          {/* Klick-Capture-Layer */}
          <rect x="0" y="0" width={dim.w} height={dim.h} fill="white" />
          <image
            href={dim.src}
            xlinkHref={dim.src}
            x="0"
            y="0"
            width={dim.w}
            height={dim.h}
            preserveAspectRatio="xMidYMid meet"
            style={{ pointerEvents: "none" }}
          />
          {markers.map((m) => (
            <Marker
              key={m.id}
              d={m}
              r={markerR}
              fs={markerFs}
              onRemove={(e) => {
                e.stopPropagation();
                onMarkerRemove(m.id);
              }}
            />
          ))}
        </svg>
      </div>
    </div>
  );
}

function Marker({ d, r, fs, onRemove }) {
  return (
    <g transform={`translate(${d.x}, ${d.y})`} style={{ cursor: "pointer" }} onClick={onRemove}>
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
        {d.type_label} – {d.zone} (Klick zum Entfernen)
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
    byType.get(d.type_label).push(d.zone);
  }
  const lines = [];
  for (const [type, zones] of byType) {
    const seen = new Set();
    const uniq = zones.filter((z) => (seen.has(z) ? false : (seen.add(z), true)));
    lines.push(`• ${type}: ${uniq.join(", ")}`);
  }
  return lines.join("\n");
}
