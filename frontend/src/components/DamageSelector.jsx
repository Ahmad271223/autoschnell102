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
  /* ---------------- FRONTANSICHT (Porsche Cayenne, 1536x1024) ----------------
     Kalibriert direkt am tatsaechlichen Bild.
     Konvention: "rechts" (Beifahrerseite) = LINKE Bildhaelfte,
                 "links"  (Fahrerseite)    = RECHTE Bildhaelfte. */
  front: [
    P("Windschutzscheibe",                   765, 140),
    P("A-Säule rechts",                      490, 130),
    P("A-Säule links",                      1050, 130),
    P("Rechter Außenspiegel",                160, 245),
    P("Linker Außenspiegel",                1385, 245),
    P("Motorhaube",                          765, 290),
    P("Marken-Emblem",                       765, 360),
    P("Rechter Hauptscheinwerfer",           445, 335),
    P("Linker Hauptscheinwerfer",           1095, 335),
    P("Kühlergrill",                         765, 475),  // grosses Mittelgitter, ueber Kennzeichen
    P("Rechter Nebelscheinwerfer",           320, 595),
    P("Linker Nebelscheinwerfer",           1215, 595),
    P("Kennzeichenhalterung",                765, 600),
    P("Rechtes Vorderrad / Reifen",          180, 745),
    P("Linkes Vorderrad / Reifen",          1355, 745),
  ],

  /* ---------------- HECKANSICHT (Porsche Cayenne) ---------------- */
  rear: [
    P("Dach",                       765, 60),
    P("Heckscheibe",                765, 170),
    P("Linker Außenspiegel",        235, 215),
    P("Rechter Außenspiegel",      1310, 215),
    P("Linkes Rücklicht",           385, 305),
    P("Rechtes Rücklicht",         1155, 305),
    P("Heckklappe",                 765, 370),
    P("Kennzeichen hinten",         765, 415),
    P("Kotflügel hinten links",     200, 470),
    P("Kotflügel hinten rechts",   1340, 470),
    P("Auspuff links",              450, 560),
    P("Auspuff rechts",            1085, 560),
    P("Stoßstange hinten",          765, 615),
    P("Linkes Hinterrad / Felge",   175, 720),
    P("Rechtes Hinterrad / Felge", 1365, 720),
  ],

  /* -------- FAHRERSEITE (Auto schaut nach LINKS, Front am LINKEN Bildrand) ------- */
  left: [
    P("Stoßstange vorne",            85, 470),
    P("Linker Hauptscheinwerfer",   130, 410),
    P("Kotflügel vorne links",      230, 430),
    P("Motorhaube",                 350, 355),
    P("Linker Außenspiegel",        555, 320),
    P("Windschutzscheibe",          490, 270),
    P("A-Säule links",              585, 240),
    P("Dach",                       780, 200),
    P("B-Säule links",              870, 350),
    P("Tür vorne links",            745, 510),
    P("Tür hinten links",          1010, 510),
    P("C-Säule links",             1085, 290),
    P("Heckscheibe",               1245, 290),
    P("Kotflügel hinten links",    1335, 430),
    P("Heckklappe",                1435, 380),
    P("Linkes Rücklicht",          1460, 470),
    P("Stoßstange hinten",         1470, 530),
    P("Schweller links",            840, 615),
    P("Vorderrad / Felge links",    320, 580),
    P("Hinterrad / Felge links",   1220, 580),
  ],

  /* ------- BEIFAHRERSEITE (Auto schaut nach RECHTS, Front am RECHTEN Bildrand) ------- */
  right: [
    P("Stoßstange vorne",          1450, 470),
    P("Rechter Hauptscheinwerfer", 1405, 410),
    P("Kotflügel vorne rechts",    1305, 430),
    P("Motorhaube",                1185, 355),
    P("Rechter Außenspiegel",       980, 320),
    P("Windschutzscheibe",         1045, 270),
    P("A-Säule rechts",             950, 240),
    P("Dach",                       755, 200),
    P("B-Säule rechts",             665, 350),
    P("Tür vorne rechts",           790, 510),
    P("Tür hinten rechts",          525, 510),
    P("C-Säule rechts",             450, 290),
    P("Heckscheibe",                290, 290),
    P("Kotflügel hinten rechts",    200, 430),
    P("Heckklappe",                 100, 380),
    P("Rechtes Rücklicht",           75, 470),
    P("Stoßstange hinten",           65, 530),
    P("Schweller rechts",           695, 615),
    P("Vorderrad / Felge rechts",  1215, 580),
    P("Hinterrad / Felge rechts",   315, 580),
  ],

  /* -------------- DRAUFSICHT (Front am RECHTEN Bildrand) --------------
     Linkslenker-Konvention beim Blick von oben mit Front rechts:
       "links"  (Fahrerseite)    = OBERE Bildhaelfte
       "rechts" (Beifahrerseite) = UNTERE Bildhaelfte  */
  top: [
    P("Stoßstange vorne",          1450, 500),
    P("Linker Hauptscheinwerfer",  1330, 290),
    P("Rechter Hauptscheinwerfer", 1330, 710),
    P("Kotflügel vorne links",     1245, 220),
    P("Kotflügel vorne rechts",    1245, 780),
    P("Motorhaube",                1120, 500),
    P("Linker Außenspiegel",        995, 175),
    P("Rechter Außenspiegel",       995, 825),
    P("A-Säule links",              935, 250),
    P("A-Säule rechts",             935, 750),
    P("Windschutzscheibe",          895, 500),
    P("Tür vorne links",            760, 270),
    P("Tür vorne rechts",           760, 730),
    P("Dach",                       620, 500),
    P("Tür hinten links",           465, 270),
    P("Tür hinten rechts",          465, 730),
    P("C-Säule links",              370, 250),
    P("C-Säule rechts",             370, 750),
    P("Heckscheibe",                325, 500),
    P("Kotflügel hinten links",     200, 220),
    P("Kotflügel hinten rechts",    200, 780),
    P("Heckklappe",                 150, 500),
    P("Linkes Rücklicht",           100, 290),
    P("Rechtes Rücklicht",          100, 710),
    P("Stoßstange hinten",           70, 500),
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
