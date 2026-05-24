import { useMemo, useState } from "react";
import { Trash2, Eraser } from "lucide-react";

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

// Alle neuen Skizzen liegen einheitlich bei 1536 × 1024 px in
// /app/frontend/public/damage/.
const IMG_W = 1536;
const IMG_H = 1024;
const VIEW_IMAGES = {
  front: { src: "/damage/front.png", w: IMG_W, h: IMG_H },
  rear:  { src: "/damage/rear.png",  w: IMG_W, h: IMG_H },
  left:  { src: "/damage/left.png",  w: IMG_W, h: IMG_H },
  right: { src: "/damage/right.png", w: IMG_W, h: IMG_H },
  top:   { src: "/damage/top.png",   w: IMG_W, h: IMG_H },
};

// Hilfsfunktion: Punkt mit Mittelpunkt (Bild-Pixel) + Name.
const P = (name, cx, cy) => ({ name, cx, cy });

/* ------------------------------------------------------------------
   DOTS — fix definierte, klickbare Punkte je Ansicht.
   Im Bild visuell als kleine, unauffällige Kreise dargestellt.
   Das Label ist im Bild UNSICHTBAR und erscheint nur im Tooltip
   beim Hover sowie später als Text im Vertrag.
   ------------------------------------------------------------------ */
const DOTS = {
  /* ---------------- FRONTANSICHT ---------------- */
  front: [
    P("Motorhaube",                          765, 480),
    P("Windschutzscheibe",                   765, 290),
    P("A-Säule rechts",                      370, 230),  // Beifahrerseite = linke Bildhälfte
    P("A-Säule links",                      1170, 230),  // Fahrerseite     = rechte Bildhälfte
    P("Rechter Außenspiegel",                290, 440),
    P("Linker Außenspiegel",                1240, 440),
    P("Marken-Emblem",                       765, 660),
    P("Rechter Hauptscheinwerfer",           490, 620),
    P("Linker Hauptscheinwerfer",           1040, 620),
    P("Kühlergrill",                         765, 800),  // über dem Kennzeichen
    P("Rechter Nebelscheinwerfer",           430, 870),
    P("Linker Nebelscheinwerfer",           1100, 870),
    P("Kennzeichenhalterung",                765, 880),
    P("Rechtes Vorderrad / Reifen",          380, 940),
    P("Linkes Vorderrad / Reifen",          1150, 940),
  ],

  /* ---------------- HECKANSICHT ---------------- */
  rear: [
    P("Dach",                       765, 180),
    P("Heckscheibe",                765, 320),
    P("Linker Außenspiegel",        440, 220),
    P("Rechter Außenspiegel",      1090, 220),
    P("Heckklappe",                 765, 560),
    P("Linkes Rücklicht",           440, 590),
    P("Rechtes Rücklicht",         1100, 590),
    P("Kennzeichen hinten",         765, 660),
    P("Kotflügel hinten links",     310, 600),
    P("Kotflügel hinten rechts",   1220, 600),
    P("Stoßstange hinten",          765, 770),
    P("Auspuff links",              620, 730),
    P("Auspuff rechts",             920, 730),
    P("Linkes Hinterrad / Felge",   320, 870),
    P("Rechtes Hinterrad / Felge", 1210, 870),
  ],

  /* -------- FAHRERSEITE (Auto schaut nach LINKS) ------- */
  left: [
    P("Stoßstange vorne",            85, 540),
    P("Linker Hauptscheinwerfer",   140, 460),
    P("Kotflügel vorne links",      230, 540),
    P("Motorhaube",                 360, 410),
    P("Linker Außenspiegel",        500, 410),
    P("Windschutzscheibe",          580, 340),
    P("A-Säule links",              530, 360),
    P("Dach",                       820, 290),
    P("B-Säule links",              820, 380),
    P("Tür vorne links",            720, 540),
    P("Tür hinten links",          1020, 540),
    P("C-Säule links",             1100, 380),
    P("Heckscheibe",               1280, 340),
    P("Kotflügel hinten links",    1330, 540),
    P("Heckklappe",                1430, 460),
    P("Linkes Rücklicht",          1450, 500),
    P("Stoßstange hinten",         1450, 580),
    P("Schweller links",            720, 690),
    P("Vorderrad / Felge links",    250, 630),
    P("Hinterrad / Felge links",   1280, 630),
  ],

  /* ------- BEIFAHRERSEITE (Auto schaut nach RECHTS) ------- */
  right: [
    P("Stoßstange vorne",          1450, 540),
    P("Rechter Hauptscheinwerfer", 1395, 460),
    P("Kotflügel vorne rechts",    1305, 540),
    P("Motorhaube",                1175, 410),
    P("Rechter Außenspiegel",      1035, 410),
    P("Windschutzscheibe",          955, 340),
    P("A-Säule rechts",            1005, 360),
    P("Dach",                       715, 290),
    P("B-Säule rechts",             715, 380),
    P("Tür vorne rechts",           815, 540),
    P("Tür hinten rechts",          515, 540),
    P("C-Säule rechts",             435, 380),
    P("Heckscheibe",                255, 340),
    P("Kotflügel hinten rechts",    205, 540),
    P("Heckklappe",                 105, 460),
    P("Rechtes Rücklicht",           85, 500),
    P("Stoßstange hinten",           85, 580),
    P("Schweller rechts",           815, 690),
    P("Vorderrad / Felge rechts",  1285, 630),
    P("Hinterrad / Felge rechts",   255, 630),
  ],

  /* -------------- DRAUFSICHT (Front rechts im Bild) -------------- */
  top: [
    P("Stoßstange vorne",          1430, 460),
    P("Linker Hauptscheinwerfer",  1340, 280),
    P("Rechter Hauptscheinwerfer", 1340, 640),
    P("Kotflügel vorne links",     1230, 230),
    P("Kotflügel vorne rechts",    1230, 690),
    P("Motorhaube",                1130, 460),
    P("Windschutzscheibe",          900, 460),
    P("A-Säule links",              930, 270),
    P("A-Säule rechts",             930, 650),
    P("Linker Außenspiegel",        960, 200),
    P("Rechter Außenspiegel",       960, 720),
    P("Tür vorne links",            780, 240),
    P("Tür vorne rechts",           780, 680),
    P("Dach",                       620, 460),
    P("Tür hinten links",           480, 240),
    P("Tür hinten rechts",          480, 680),
    P("C-Säule links",              400, 270),
    P("C-Säule rechts",             400, 650),
    P("Heckscheibe",                340, 460),
    P("Kotflügel hinten links",     220, 230),
    P("Kotflügel hinten rechts",    220, 690),
    P("Heckklappe",                 180, 460),
    P("Linkes Rücklicht",           120, 280),
    P("Rechtes Rücklicht",          120, 640),
    P("Stoßstange hinten",          100, 460),
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

export default function DamageSelector({ damages = [], onChange }) {
  const [activeType, setActiveType] = useState(DAMAGE_TYPES[5]); // default: Kratzer

  const handleDotClick = (view, dot) => {
    if (!activeType) return;
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
          wählen → in einer der Skizzen auf einen der kleinen Punkte klicken.
          Hover zeigt den Namen, nach dem Klick wird der Eintrag automatisch
          in den Vertrag übernommen.
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

      {/* 5 Ansichten gleichzeitig — responsive Grid */}
      <div
        className="grid grid-cols-1 md:grid-cols-2 gap-3"
        data-testid="damage-grid"
      >
        <ViewCard view="front" markers={grouped.front || []}
                  activeColor={activeType?.color}
                  onDotClick={handleDotClick}
                  onSvgClick={handleSvgClick}
                  onMarkerRemove={removeDamage} />
        <ViewCard view="rear"  markers={grouped.rear  || []}
                  activeColor={activeType?.color}
                  onDotClick={handleDotClick}
                  onSvgClick={handleSvgClick}
                  onMarkerRemove={removeDamage} />
        <ViewCard view="left"  markers={grouped.left  || []}
                  activeColor={activeType?.color}
                  onDotClick={handleDotClick}
                  onSvgClick={handleSvgClick}
                  onMarkerRemove={removeDamage} />
        <ViewCard view="right" markers={grouped.right || []}
                  activeColor={activeType?.color}
                  onDotClick={handleDotClick}
                  onSvgClick={handleSvgClick}
                  onMarkerRemove={removeDamage} />
        <ViewCard view="top"   markers={grouped.top   || []}
                  activeColor={activeType?.color}
                  onDotClick={handleDotClick}
                  onSvgClick={handleSvgClick}
                  onMarkerRemove={removeDamage} />
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

function ViewCard({ view, markers, activeColor, onDotClick, onSvgClick, onMarkerRemove, className = "" }) {
  const dim = VIEW_IMAGES[view];
  const dots = DOTS[view] || [];
  const markerR = view === "top" ? 28 : 26;
  const markerFs = view === "top" ? 24 : 22;
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
        className="relative w-full overflow-hidden rounded-md"
        style={{ aspectRatio: `${dim.w} / ${dim.h}` }}
      >
        <svg
          viewBox={`0 0 ${dim.w} ${dim.h}`}
          preserveAspectRatio="none"
          width="100%"
          height="100%"
          className="select-none block absolute inset-0"
          onClick={(e) => onSvgClick(view, e)}
          data-testid={`damage-svg-${view}`}
          style={{ touchAction: "manipulation" }}
        >
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

          {/* Klickbare Dots — unauffällig, Label im title (Hover-Tooltip) */}
          {dots.map((d) => (
            <Dot
              key={d.name}
              dot={d}
              r={dotR}
              haloR={dotHaloR}
              activeColor={activeColor || "#0ea5e9"}
              onClick={(e) => {
                e.stopPropagation();
                onDotClick(view, d);
              }}
            />
          ))}

          {/* Bereits gesetzte Marker */}
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

/** Unauffälliger Klick-Punkt im Bild — Label nur als <title>-Tooltip,
 *  visuell ein kleiner halbtransparenter Kreis. Hover-Halo macht den
 *  Trefferbereich für die Maus großzügig. */
function Dot({ dot, r, haloR, activeColor, onClick }) {
  return (
    <g transform={`translate(${dot.cx}, ${dot.cy})`}
       style={{ cursor: "pointer" }}
       className="damage-dot"
       onClick={onClick}>
      {/* Unsichtbarer Hover-Halo — vergrößert den Hit-Bereich */}
      <circle r={haloR} fill="transparent" />
      {/* Sichtbarer Punkt — wechselt bei Hover zur aktiven Schadensfarbe */}
      <circle
        r={r}
        className="damage-dot-inner"
        fill="rgba(15,23,42,0.18)"
        stroke="rgba(15,23,42,0.55)"
        strokeWidth="2.5"
      />
      <title>{dot.name}</title>
      <style>{`
        .damage-dot:hover .damage-dot-inner {
          fill: ${activeColor};
          stroke: #0a0a0a;
          stroke-width: 3;
          opacity: 0.95;
        }
      `}</style>
    </g>
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
